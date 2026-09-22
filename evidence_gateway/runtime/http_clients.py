"""Fixed-operation HTTPS clients. Connections and credential readers are injected."""

from __future__ import annotations

import base64
import json
import re
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from ..canonical import canonical_json
from ..policy import SHA_RE
from ..providers import ProviderError
from .gateway import EXTERNAL_TOKEN_AUDIENCE, VALIDATION_KUBERNETES_SUBJECT, TokenReviewResult
from .source_contract import (
    SOURCE_STATE_PATH, SOURCE_REVISION_PATH, SOURCE_STATE_MAX_BYTES, SOURCE_REVISION_MAX_BYTES,
    project_frontend_state, project_deployment_revision,
)
from .transports import (
    GITOPS_PATHS, GITHUB_ENVELOPE_MAX_BYTES, GITHUB_PROXY_DNS_SAN, GITHUB_PROXY_URI_SAN,
    SOURCE_DNS_SAN, SOURCE_URI_SAN, GitHubProxyResponse, InternalMTLSValidator,
    PeerCertificate, _decode_github_contents,
)

TOKENREVIEW_HOST = "kubernetes.default.svc"
TOKENREVIEW_PATH = "/apis/authentication.k8s.io/v1/tokenreviews"
_GITOPS_PATHS = MappingProxyType(dict(GITOPS_PATHS))
_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/-]+=*", re.ASCII)
_SAFE_ERROR = "approved HTTPS transport is unavailable"


class HTTPResponse(Protocol):
    """read must honor size and timeout; headers are bounded by the executor."""

    status: int
    headers: Mapping[str, str]

    def read(self, size: int, *, timeout: float) -> bytes: ...


class TLSConnection(Protocol):
    """Peer facts must come from this connection's verified TLS handshake."""

    def peer_certificate(self) -> PeerCertificate: ...

    def request(
        self, *, method: str, path: str, headers: Mapping[str, str], body: bytes, timeout: float,
    ) -> HTTPResponse: ...

    def close(self) -> None: ...


class HTTPExecutor(Protocol):
    """Trusted I/O boundary: TLS only, no proxies, redirects, retries, or fallback.

    open_tls completes certificate-chain and hostname verification before return.
    It must use the supplied SSLContext, SNI, timeout, and certificate chain.
    A failed open must close any partially opened connection itself.
    """

    def open_tls(
        self, *, host: str, port: int, server_hostname: str, context: ssl.SSLContext, timeout: float,
    ) -> TLSConnection: ...


@dataclass(frozen=True)
class TLSConfiguration:
    """Operator-supplied file locations; no files are read until a request runs."""

    ca_file: str = field(repr=False)
    certificate_file: str | None = field(default=None, repr=False)
    private_key_file: str | None = field(default=None, repr=False)
    client_identity: PeerCertificate | None = field(default=None, repr=False)

    def build_context(
        self, *, mutual: bool, now: datetime,
        context_factory: Callable[..., ssl.SSLContext] = ssl.SSLContext,
    ) -> ssl.SSLContext:
        try:
            if not isinstance(self.ca_file, str) or not self.ca_file:
                raise ValueError()
            context = context_factory(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = True
            context.hostname_checks_common_name = False
            context.load_verify_locations(cafile=self.ca_file)
            if mutual:
                if not all(isinstance(value, str) and value for value in (self.certificate_file, self.private_key_file)):
                    raise ValueError()
                InternalMTLSValidator().validate_client(self.client_identity, now=now)
                context.load_cert_chain(certfile=self.certificate_file, keyfile=self.private_key_file)
            elif any(value is not None for value in (self.certificate_file, self.private_key_file, self.client_identity)):
                raise ValueError()
            if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
                raise ValueError()
            return context
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None


class _HTTPSClient:
    def __init__(
        self, *, executor: HTTPExecutor, tls: TLSConfiguration,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        context_factory: Callable[..., ssl.SSLContext] = ssl.SSLContext,
    ) -> None:
        self._executor = executor
        self._tls = tls
        self._clock = clock
        self._monotonic = monotonic
        self._context_factory = context_factory

    def _exchange(
        self, *, host: str, port: int, uri: str | None, method: str, path: str,
        limit: int, body: bytes = b"", authorization: str | None = None,
    ) -> bytes:
        connection = None
        try:
            deadline = self._monotonic() + 5.0

            def remaining() -> float:
                value = deadline - self._monotonic()
                if value <= 0:
                    raise TimeoutError()
                return value

            context = self._tls.build_context(
                mutual=uri is not None, now=self._clock(), context_factory=self._context_factory,
            )
            connection = self._executor.open_tls(
                host=host, port=port, server_hostname=host, context=context, timeout=remaining(),
            )
            peer = connection.peer_certificate()
            validator = InternalMTLSValidator()
            if uri is not None:
                validator.validate_server(peer, expected_dns=host, expected_uri=uri, now=self._clock())
            else:
                validator._validate_common(peer, self._clock())
                if host not in peer.dns_names or peer.extended_key_usages != frozenset({"serverAuth"}):
                    raise ValueError()
            headers = {"Host": host, "Accept": "application/json", "Accept-Encoding": "identity"}
            if body:
                headers["Content-Type"] = "application/json"
            if authorization is not None:
                headers["Authorization"] = authorization
            response = connection.request(method=method, path=path, headers=headers, body=body, timeout=remaining())
            if type(response.status) is not int or response.status != (201 if method == "POST" else 200):
                # Never read or forward backend error bodies, including 403/429.
                raise ValueError()
            response_headers = _headers(response.headers)
            if response_headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError()
            if response_headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError()
            length = response_headers.get("content-length")
            if length is not None and (not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > limit):
                raise ValueError()
            result = bytearray()
            while True:
                size = min(4096, limit + 1 - len(result))
                chunk = response.read(size, timeout=remaining())
                remaining()
                if not isinstance(chunk, bytes) or len(chunk) > size:
                    raise ValueError()
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > limit:
                    raise ValueError()
            if length is not None and len(result) != int(length):
                raise ValueError()
            return bytes(result)
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    raise ProviderError(_SAFE_ERROR) from None


class KubernetesTokenReviewClient(_HTTPSClient):
    def __init__(self, *, runtime_token_reader: Callable[[], str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._runtime_token_reader = runtime_token_reader

    def review(self, *, token: str, audience: str) -> TokenReviewResult:
        try:
            if audience != EXTERNAL_TOKEN_AUDIENCE:
                raise ValueError()
            _token(token)
            runtime_token = self._runtime_token_reader()
            _token(runtime_token)
            body = canonical_json({
                "apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
                "spec": {"token": token, "audiences": [EXTERNAL_TOKEN_AUDIENCE]},
            }).encode("utf-8")
            value = _json(self._exchange(
                host=TOKENREVIEW_HOST, port=443, uri=None, method="POST", path=TOKENREVIEW_PATH,
                limit=16 * 1024, body=body, authorization="Bearer " + runtime_token,
            ))
            if value.get("apiVersion") != "authentication.k8s.io/v1" or value.get("kind") != "TokenReview":
                raise ValueError()
            status = value.get("status")
            if not isinstance(status, dict) or type(status.get("authenticated")) is not bool:
                raise ValueError()
            if status.get("error") or status["authenticated"] is False:
                return TokenReviewResult(False, None, frozenset())
            user = status.get("user")
            audiences = status.get("audiences")
            if not isinstance(user, dict) or user.get("username") != VALIDATION_KUBERNETES_SUBJECT:
                raise ValueError()
            if audiences != [EXTERNAL_TOKEN_AUDIENCE]:
                raise ValueError()
            return TokenReviewResult(True, VALIDATION_KUBERNETES_SUBJECT, frozenset({EXTERNAL_TOKEN_AUDIENCE}))
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None


class SourceHTTPClient(_HTTPSClient):
    def get_frontend_state(self) -> dict[str, Any]:
        try:
            value = _json(self._exchange(
                host=SOURCE_DNS_SAN, port=8443, uri=SOURCE_URI_SAN,
                method="GET", path=SOURCE_STATE_PATH, limit=SOURCE_STATE_MAX_BYTES,
            ))
            return project_frontend_state(value)
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None

    def get_deployment_revision(self) -> dict[str, Any]:
        try:
            value = _json(self._exchange(
                host=SOURCE_DNS_SAN, port=8443, uri=SOURCE_URI_SAN,
                method="GET", path=SOURCE_REVISION_PATH, limit=SOURCE_REVISION_MAX_BYTES,
            ))
            return project_deployment_revision(value)
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None


class GitHubEgressHTTPClient(_HTTPSClient):
    def read_allowlisted_contents(self, *, path_id: str, revision: str, max_response_bytes: int) -> GitHubProxyResponse:
        try:
            if not isinstance(path_id, str) or path_id not in _GITOPS_PATHS:
                raise ValueError()
            if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
                raise ValueError()
            if type(max_response_bytes) is not int or not 0 < max_response_bytes <= GITHUB_ENVELOPE_MAX_BYTES:
                raise ValueError()
            path = f"/repos/DimitryZH/sre-platform/contents/{_GITOPS_PATHS[path_id]}?ref={revision}"
            body = self._exchange(
                host=GITHUB_PROXY_DNS_SAN, port=8443, uri=GITHUB_PROXY_URI_SAN,
                method="GET", path=path, limit=max_response_bytes,
            )
            envelope = _json(body)
            # Validate decoded size as well as streamed JSON size; retain only allowed fields.
            projected = {key: envelope[key] for key in ("type", "path", "encoding", "content")}
            normalized = canonical_json(projected).encode("utf-8")
            decoded = _decode_github_contents(normalized, path_id=path_id, revision=revision, max_bytes=4096)
            projected["content"] = base64.b64encode(decoded["content"].encode("utf-8")).decode("ascii")
            normalized = canonical_json(projected).encode("utf-8")
            if len(normalized) > max_response_bytes:
                raise ValueError()
            return GitHubProxyResponse(200, normalized)
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None


def _headers(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 64:
        raise ValueError()
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str) or len(key) + len(item) > 8192:
            raise ValueError()
        if key.lower() in result:
            raise ValueError()
        result[key.lower()] = item
    return result


def _json(body: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise ValueError()

    value = json.loads(body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError()
    return value


def _token(value: Any) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 8192 or not _TOKEN_RE.fullmatch(value):
        raise ValueError()
