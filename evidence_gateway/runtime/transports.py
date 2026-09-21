"""Typed, mTLS-validated runtime transports with no live client implementation."""

from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from ..adapters import APPROVED_TARGET, GITOPS_PATHS
from ..policy import MAX_GITOPS_FILE_BYTES, SHA_RE
from ..providers import ProviderCalls, ProviderError

GATEWAY_URI_SAN = "spiffe://evidence-gateway-stage/gateway"
SOURCE_URI_SAN = "spiffe://evidence-gateway-stage/source"
GITHUB_PROXY_URI_SAN = "spiffe://evidence-gateway-stage/github-egress"
SOURCE_DNS_SAN = "evidence-source-stage.evidence-gateway-stage.svc"
GITHUB_PROXY_DNS_SAN = "github-egress.evidence-gateway-stage.svc"
GITHUB_ENVELOPE_MAX_BYTES = 16 * 1024


@dataclass(frozen=True)
class PeerCertificate:
    """Normalized certificate facts supplied by a future TLS implementation."""

    trusted_by_configured_ca: bool
    dns_names: frozenset[str]
    uri_sans: frozenset[str]
    not_after: datetime


class InternalMTLSValidator:
    """Fail-closed verifier for the two internal service identities."""

    def validate_client(self, certificate: PeerCertificate, *, now: datetime) -> None:
        self._validate_common(certificate, now)
        if certificate.uri_sans != frozenset({GATEWAY_URI_SAN}):
            raise ProviderError("internal client identity is unavailable")

    def validate_server(
        self,
        certificate: PeerCertificate,
        *,
        expected_dns: str,
        expected_uri: str,
        now: datetime,
    ) -> None:
        self._validate_common(certificate, now)
        if certificate.dns_names != frozenset({expected_dns}) or certificate.uri_sans != frozenset({expected_uri}):
            raise ProviderError("internal server identity is unavailable")

    @staticmethod
    def _validate_common(certificate: PeerCertificate, now: datetime) -> None:
        if not certificate.trusted_by_configured_ca:
            raise ProviderError("internal trust bundle is unavailable")
        if certificate.not_after.tzinfo is None or certificate.not_after.astimezone(UTC) <= now.astimezone(UTC):
            raise ProviderError("internal certificate is unavailable")


class SourceServiceTransport(Protocol):
    """Only the two narrow source operations available to the runtime."""

    def get_frontend_state(self) -> dict[str, Any]: ...

    def get_deployment_revision(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GitHubProxyResponse:
    status: int
    body: bytes


class GitHubProxyTransport(Protocol):
    """The proxy receives an allowlisted identifier, immutable SHA, and bound size."""

    def read_allowlisted_contents(
        self, *, path_id: str, revision: str, max_response_bytes: int
    ) -> GitHubProxyResponse: ...


@dataclass
class MTLSSourceClient:
    transport: SourceServiceTransport
    client_certificate: PeerCertificate
    server_certificate: PeerCertificate
    validator: InternalMTLSValidator = field(default_factory=InternalMTLSValidator)

    def get_frontend_state(self, *, now: datetime) -> dict[str, Any]:
        self._verify(now, SOURCE_DNS_SAN, SOURCE_URI_SAN)
        try:
            value = self.transport.get_frontend_state()
        except Exception as exc:
            raise ProviderError("source service is unavailable") from exc
        if not isinstance(value, dict):
            raise ProviderError("source service response is malformed")
        return deepcopy(value)

    def get_deployment_revision(self, *, now: datetime) -> dict[str, Any]:
        self._verify(now, SOURCE_DNS_SAN, SOURCE_URI_SAN)
        try:
            value = self.transport.get_deployment_revision()
        except Exception as exc:
            raise ProviderError("source service is unavailable") from exc
        if not isinstance(value, dict):
            raise ProviderError("source service response is malformed")
        return deepcopy(value)

    def _verify(self, now: datetime, dns_name: str, uri_san: str) -> None:
        self.validator.validate_client(self.client_certificate, now=now)
        self.validator.validate_server(self.server_certificate, expected_dns=dns_name, expected_uri=uri_san, now=now)


@dataclass
class MTLSGitHubProxyClient:
    transport: GitHubProxyTransport
    client_certificate: PeerCertificate
    server_certificate: PeerCertificate
    validator: InternalMTLSValidator = field(default_factory=InternalMTLSValidator)

    def read_allowlisted_contents(self, *, path_id: str, revision: str, now: datetime) -> GitHubProxyResponse:
        self.validator.validate_client(self.client_certificate, now=now)
        self.validator.validate_server(
            self.server_certificate,
            expected_dns=GITHUB_PROXY_DNS_SAN,
            expected_uri=GITHUB_PROXY_URI_SAN,
            now=now,
        )
        try:
            response = self.transport.read_allowlisted_contents(
                path_id=path_id,
                revision=revision,
                max_response_bytes=GITHUB_ENVELOPE_MAX_BYTES,
            )
        except Exception as exc:
            raise ProviderError("github proxy is unavailable") from exc
        if not isinstance(response, GitHubProxyResponse) or not isinstance(response.status, int) or not isinstance(response.body, bytes):
            raise ProviderError("github proxy response is malformed")
        if len(response.body) > GITHUB_ENVELOPE_MAX_BYTES:
            raise ProviderError("github proxy response exceeded limit")
        if response.status in {403, 429} or response.status < 200 or response.status >= 300:
            raise ProviderError("github proxy is unavailable")
        return response


@dataclass
class RuntimeKubernetesProvider:
    """State only; dynamic pod, event, and log evidence remains unavailable."""

    source: MTLSSourceClient
    clock: Callable[[], datetime]
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_state(self, *, target: dict[str, Any]) -> dict[str, Any]:
        _require_target(target)
        self.calls.increment("kubernetes.get_frontend_state", {"target": APPROVED_TARGET})
        state = self.source.get_frontend_state(now=self.clock())
        rollout = state.get("rollout")
        if not isinstance(rollout, dict):
            raise ProviderError("source service response is malformed")
        # AnalysisRuns do not have an approved runtime contract in this slice.
        if rollout.get("analysis_runs", []) not in ([], None):
            raise ProviderError("analysis run evidence is unavailable")
        normalized = deepcopy(state)
        normalized["rollout"] = dict(rollout)
        normalized["rollout"]["analysis_runs"] = []
        return normalized

    def get_frontend_pod_status(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("pod status evidence is unavailable")

    def get_frontend_events(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("event evidence is unavailable")


@dataclass
class RuntimeUnavailableLogsProvider:
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_container_logs(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("log evidence is unavailable")


@dataclass
class RuntimeUnavailablePrometheusProvider:
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def query_template(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        raise ProviderError("prometheus evidence is unavailable")


@dataclass
class RuntimeGitOpsProvider:
    source: MTLSSourceClient
    github: MTLSGitHubProxyClient
    clock: Callable[[], datetime]
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_deployment_revision(self, *, target: dict[str, Any]) -> dict[str, Any]:
        _require_target(target)
        self.calls.increment("gitops.get_deployment_revision", {"target": APPROVED_TARGET})
        return _require_application(self.source.get_deployment_revision(now=self.clock()))

    def read_file_at_revision(
        self, path_id: str, revision: str, *, target: dict[str, Any], max_bytes: int
    ) -> dict[str, Any]:
        _require_target(target)
        if path_id not in GITOPS_PATHS or not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
            raise ProviderError("gitops request is outside the approved boundary")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_GITOPS_FILE_BYTES:
            raise ProviderError("gitops file limit is outside the approved boundary")
        current = _require_application(self.source.get_deployment_revision(now=self.clock()))
        if current["revision"] != revision:
            raise ProviderError("gitops revision changed")
        self.calls.increment(
            f"gitops.read_file_at_revision.{path_id}",
            {"path_id": path_id, "revision": revision, "target": APPROVED_TARGET, "max_bytes": max_bytes},
        )
        response = self.github.read_allowlisted_contents(path_id=path_id, revision=revision, now=self.clock())
        return _decode_github_contents(response.body, path_id=path_id, revision=revision, max_bytes=max_bytes)


def _require_target(target: Any) -> None:
    if target != APPROVED_TARGET:
        raise ProviderError("target is outside the approved boundary")


def _require_application(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {"application", "revision", "sync_status", "health_status"}:
        raise ProviderError("source service response is malformed")
    if value["application"] != APPROVED_TARGET["argocd_application"] or not isinstance(value["revision"], str):
        raise ProviderError("source service response is outside the approved boundary")
    if not SHA_RE.fullmatch(value["revision"]):
        raise ProviderError("source service revision is malformed")
    if not all(isinstance(value[key], str) for key in ("sync_status", "health_status")):
        raise ProviderError("source service response is malformed")
    return deepcopy(value)


def _decode_github_contents(body: bytes, *, path_id: str, revision: str, max_bytes: int) -> dict[str, Any]:
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("github proxy response is malformed") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"path", "encoding", "content"}:
        raise ProviderError("github proxy response is malformed")
    if envelope["path"] != GITOPS_PATHS[path_id] or envelope["encoding"] != "base64" or not isinstance(envelope["content"], str):
        raise ProviderError("github proxy response is outside the approved boundary")
    try:
        decoded = base64.b64decode(envelope["content"], validate=True)
        content = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ProviderError("github proxy content is malformed") from exc
    if len(decoded) > max_bytes:
        raise ProviderError("github proxy content exceeded limit")
    return {"path": GITOPS_PATHS[path_id], "revision": revision, "content": content}
