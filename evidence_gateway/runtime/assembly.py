"""Runnable composition for the two approved Evidence Gateway runtime roles."""

from __future__ import annotations

import argparse
import ipaddress
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..adapters import APPROVED_TARGET, ArgoCDGitOpsAdapter, KubernetesEvidenceAdapter
from ..policy import EvidenceError
from ..providers import EvidenceProviders, ProviderCalls, ProviderError
from .gateway import MAX_HTTP_REQUEST_BYTES, RuntimeEvidencePolicy, RuntimeGateway, RuntimeResponse, RuntimeTokenConfig
from .http_clients import KubernetesObjectHTTPClient, KubernetesTokenReviewClient, SourceHTTPClient, TLSConfiguration
from .kubernetes_backends import NarrowArgoCDBackend, NarrowKubernetesBackend, UnavailableGitOpsBackend
from .source_service import PrivateSourceService
from .state import RuntimeState
from .tls_runtime import StdlibHTTPExecutor, build_source_server_context, certificate_from_der, certificate_from_file
from .transports import (
    GATEWAY_URI_SAN, InternalMTLSValidator, RuntimeUnavailableLogsProvider,
    RuntimeUnavailablePrometheusProvider,
)

GATEWAY_ROLE = "gateway"
SOURCE_ROLE = "source"
RUNTIME_STATE_PATH = "/var/lib/evidence-gateway/runtime.db"
ALLOWED_EVIDENCE_KINDS = frozenset({"kubernetes_state", "deployment_revision"})
MAX_TOKEN_FILE_BYTES = 8192
_TOKEN_FILE_RE = re.compile(rb"[A-Za-z0-9._~+/-]+=*", re.ASCII)


class RuntimeAssemblyError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    role: str
    listen_address: str
    listen_port: int
    internal_ca_file: str
    kubernetes_ca_file: str
    certificate_file: str
    private_key_file: str
    projected_token_file: str

    def validate(self) -> None:
        try:
            if self.role not in {GATEWAY_ROLE, SOURCE_ROLE}:
                raise ValueError()
            if not isinstance(self.listen_address, str):
                raise ValueError()
            ipaddress.ip_address(self.listen_address)
            if type(self.listen_port) is not int or not 1 <= self.listen_port <= 65535:
                raise ValueError()
            if self.role == SOURCE_ROLE and self.listen_port != 8443:
                raise ValueError()
            for value in (
                self.internal_ca_file, self.kubernetes_ca_file, self.certificate_file,
                self.private_key_file, self.projected_token_file,
            ):
                path = Path(value)
                if not isinstance(value, str) or not value or not path.is_absolute() or not path.is_file():
                    raise ValueError()
        except (TypeError, ValueError, OSError):
            raise RuntimeAssemblyError("runtime configuration is unavailable") from None

    @classmethod
    def from_args(cls, argv: Sequence[str] | None = None) -> RuntimeConfig:
        parser = argparse.ArgumentParser(prog="evidence-gateway-runtime", allow_abbrev=False)
        parser.add_argument("--role", required=True, choices=(GATEWAY_ROLE, SOURCE_ROLE))
        parser.add_argument("--listen-address", required=True)
        parser.add_argument("--listen-port", required=True, type=int)
        parser.add_argument("--internal-ca-file", required=True)
        parser.add_argument("--kubernetes-ca-file", required=True)
        parser.add_argument("--certificate-file", required=True)
        parser.add_argument("--private-key-file", required=True)
        parser.add_argument("--projected-token-file", required=True)
        values = vars(parser.parse_args(argv))
        config = cls(**values)
        config.validate()
        return config


class RuntimeAssemblyPolicy(RuntimeEvidencePolicy):
    def validate_request_context(self, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        approved = super().validate_request_context(request, now=now)
        if not set(approved["evidence_kinds"]).issubset(ALLOWED_EVIDENCE_KINDS):
            raise EvidenceError("unsupported_evidence_kind", "requested evidence is unavailable")
        return approved


@dataclass
class SourceStateProvider:
    source: SourceHTTPClient
    calls: ProviderCalls

    def get_frontend_state(self, *, target: dict[str, Any]) -> dict[str, Any]:
        _target(target)
        self.calls.increment("kubernetes.get_frontend_state", {"target": APPROVED_TARGET})
        return self.source.get_frontend_state()

    def get_frontend_pod_status(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("pod status evidence is unavailable")

    def get_frontend_events(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("event evidence is unavailable")


@dataclass
class SourceRevisionProvider:
    source: SourceHTTPClient
    calls: ProviderCalls

    def get_deployment_revision(self, *, target: dict[str, Any]) -> dict[str, Any]:
        _target(target)
        self.calls.increment("gitops.get_deployment_revision", {"target": APPROVED_TARGET})
        return self.source.get_deployment_revision()

    def read_file_at_revision(self, *_: Any, **__: Any) -> dict[str, Any]:
        raise ProviderError("gitops contents evidence is unavailable")


@dataclass
class BuiltRuntime:
    role: str
    application: RuntimeGateway | PrivateSourceService
    server_context: ssl.SSLContext | None
    state: RuntimeState | None = None

    def close(self) -> None:
        if self.state is not None:
            self.state.close()


def build_runtime(
    config: RuntimeConfig, *, executor: Any | None = None,
    state_path: str = RUNTIME_STATE_PATH, now: datetime | None = None,
    server_context_factory: Any = ssl.SSLContext,
) -> BuiltRuntime:
    config.validate()
    current = now or datetime.now(UTC)
    transport = executor or StdlibHTTPExecutor()
    token_reader = _token_reader(config.projected_token_file)
    token_reader()
    api_tls = TLSConfiguration(config.kubernetes_ca_file)
    api_tls.build_context(mutual=False, now=current)

    if config.role == GATEWAY_ROLE:
        client_identity = certificate_from_file(config.certificate_file)
        InternalMTLSValidator().validate_client(client_identity, now=current)
        source_tls = TLSConfiguration(
            config.internal_ca_file, config.certificate_file, config.private_key_file, client_identity,
        )
        source_tls.build_context(mutual=True, now=current)
        source = SourceHTTPClient(
            executor=transport,
            tls=source_tls,
        )
        providers = EvidenceProviders(
            SourceStateProvider(source, ProviderCalls()),
            RuntimeUnavailablePrometheusProvider(),
            RuntimeUnavailableLogsProvider(),
            SourceRevisionProvider(source, ProviderCalls()),
        )
        try:
            state = RuntimeState(state_path)
            gateway = RuntimeGateway(
                token_reviewer=KubernetesTokenReviewClient(
                    executor=transport, tls=api_tls, runtime_token_reader=token_reader,
                ),
                providers=providers,
                state=state,
                runtime_token=RuntimeTokenConfig(path=config.projected_token_file),
                policy=RuntimeAssemblyPolicy(),
            )
            return BuiltRuntime(GATEWAY_ROLE, gateway, None, state)
        except Exception:
            if "state" in locals():
                state.close()
            raise RuntimeAssemblyError("gateway runtime is unavailable") from None

    server_context = build_source_server_context(
        ca_file=config.internal_ca_file,
        certificate_file=config.certificate_file,
        private_key_file=config.private_key_file,
        now=current,
        context_factory=server_context_factory,
    )
    client = KubernetesObjectHTTPClient(
        executor=transport, tls=api_tls, runtime_token_reader=token_reader,
    )
    service = PrivateSourceService(
        kubernetes=KubernetesEvidenceAdapter(NarrowKubernetesBackend(client)),
        gitops=ArgoCDGitOpsAdapter(NarrowArgoCDBackend(client), UnavailableGitOpsBackend()),
    )
    return BuiltRuntime(SOURCE_ROLE, service, server_context)


def serve(config: RuntimeConfig) -> None:
    runtime = build_runtime(config)
    server: ThreadingHTTPServer | None = None
    try:
        server = ThreadingHTTPServer((config.listen_address, config.listen_port), RuntimeRequestHandler)
        server.runtime = runtime  # type: ignore[attr-defined]
        server.daemon_threads = True
        if runtime.server_context is not None:
            server.socket = runtime.server_context.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
    except Exception:
        raise RuntimeAssemblyError("runtime listener is unavailable") from None
    finally:
        if server is not None:
            server.server_close()
        runtime.close()


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "evidence-runtime"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def log_message(self, *_: Any) -> None:
        return None

    def _dispatch(self) -> None:
        runtime = getattr(self.server, "runtime", None)
        if not isinstance(runtime, BuiltRuntime):
            self._write(RuntimeResponse(503, {"outcome": "denied", "error": {"code": "backend_unavailable"}}))
            return
        try:
            headers = _request_headers(self.headers.raw_items())
            if runtime.role == GATEWAY_ROLE:
                body = _gateway_body(self.rfile, headers)
                response = runtime.application.handle(
                    method=self.command, path=self.path, headers=headers, body=body,
                )
            else:
                peer = _source_peer(self.connection)
                body = b"" if _empty_body(headers) else b"\x00"
                response = runtime.application.handle(
                    method=self.command, path=self.path, headers=headers, body=body, peer=peer,
                )
        except Exception:
            response = RuntimeResponse(503, {"outcome": "denied", "error": {"code": "backend_unavailable"}})
        self._write(response)

    def _write(self, response: RuntimeResponse) -> None:
        payload = response.json_bytes()
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True


def _source_peer(connection: Any) -> Any:
    if not isinstance(connection, ssl.SSLSocket):
        raise RuntimeAssemblyError("plaintext source request is unavailable")
    return certificate_from_der(connection.getpeercert(binary_form=True), trusted=True)


def _request_headers(items: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    total = 0
    for name, value in items:
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError()
        key = name.lower()
        total += len(name) + len(value) + 4
        if key in result or len(result) >= 16 or total > 8192:
            raise ValueError()
        result[name] = value
    return result


def _gateway_body(stream: Any, headers: Mapping[str, str]) -> bytes:
    values = {key.lower(): value for key, value in headers.items()}
    if "transfer-encoding" in values:
        return b"\x00" * (MAX_HTTP_REQUEST_BYTES + 1)
    length_text = values.get("content-length")
    if length_text is None or not length_text.isascii() or not length_text.isdecimal():
        return b"\x00" * (MAX_HTTP_REQUEST_BYTES + 1)
    length = int(length_text)
    if length > MAX_HTTP_REQUEST_BYTES:
        return b"\x00" * (MAX_HTTP_REQUEST_BYTES + 1)
    body = stream.read(length)
    return body if isinstance(body, bytes) and len(body) == length else b"\x00" * (MAX_HTTP_REQUEST_BYTES + 1)


def _empty_body(headers: Mapping[str, str]) -> bool:
    values = {key.lower(): value for key, value in headers.items()}
    return "transfer-encoding" not in values and values.get("content-length", "0") == "0"


def _token_reader(path: str) -> Callable[[], str]:
    token_path = Path(path)

    def read() -> str:
        try:
            with token_path.open("rb") as stream:
                value = stream.read(MAX_TOKEN_FILE_BYTES + 1)
            if not value or len(value) > MAX_TOKEN_FILE_BYTES:
                raise ValueError()
            token = value[:-1] if value.endswith(b"\n") else value
            token = token[:-1] if token.endswith(b"\r") else token
            if not token or not _TOKEN_FILE_RE.fullmatch(token):
                raise ValueError()
            return token.decode("ascii")
        except Exception:
            raise ProviderError("projected runtime token is unavailable") from None

    return read


def _target(value: Any) -> None:
    if value != APPROVED_TARGET:
        raise ProviderError("target is outside the approved boundary")
