"""Private fixed-route source boundary using injected offline-contract adapters."""

from datetime import UTC, datetime
from typing import Callable, Mapping

from ..adapters import ArgoCDGitOpsAdapter, KubernetesEvidenceAdapter
from ..policy import TargetPolicy
from .gateway import RuntimeResponse
from .source_contract import (
    SOURCE_STATE_PATH, SOURCE_REVISION_PATH, SOURCE_STATE_MAX_BYTES, SOURCE_REVISION_MAX_BYTES,
    project_frontend_state, project_deployment_revision,
)
from .transports import SOURCE_DNS_SAN, InternalMTLSValidator, PeerCertificate


class PrivateSourceService:
    """Dispatcher, not a listener. Peer facts belong to the trusted TLS connection.

    The transport must supply the unmodified request target, bounded headers,
    and an empty body, never facts derived from HTTP headers or body fields.
    Injected adapter backends must honor their narrow normalized read contracts.
    """

    def __init__(
        self, *, kubernetes: KubernetesEvidenceAdapter, gitops: ArgoCDGitOpsAdapter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._kubernetes = kubernetes
        self._gitops = gitops
        self._clock = clock

    def handle(
        self, *, method: str, path: str, headers: Mapping[str, str], body: bytes,
        peer: PeerCertificate,
    ) -> RuntimeResponse:
        try:
            InternalMTLSValidator().validate_client(peer, now=self._clock())
        except Exception:
            return _failure(401, "unauthorized")
        if type(method) is not str or method != "GET" or type(path) is not str or path not in (
            SOURCE_STATE_PATH, SOURCE_REVISION_PATH,
        ):
            return _failure(404, "unknown_operation")
        try:
            if not isinstance(body, bytes) or body != b"":
                raise ValueError()
            _validate_headers(headers)
        except Exception:
            return _failure(400, "malformed_request")

        try:
            # Fresh server-owned target; nothing from the request reaches an adapter.
            target = TargetPolicy().to_public_dict()
            if path == SOURCE_STATE_PATH:
                result = project_frontend_state(self._kubernetes.get_frontend_state(target=target))
                limit = SOURCE_STATE_MAX_BYTES
            else:
                result = project_deployment_revision(self._gitops.get_deployment_revision(target=target))
                limit = SOURCE_REVISION_MAX_BYTES
            response = RuntimeResponse(200, result)
            if len(response.json_bytes()) > limit:
                raise ValueError()
            return response
        except Exception:
            return _failure(503, "backend_unavailable")


def _validate_headers(headers: Mapping[str, str]) -> None:
    if not isinstance(headers, Mapping) or len(headers) > 4:
        raise ValueError()
    allowed = {
        "host": (SOURCE_DNS_SAN, SOURCE_DNS_SAN + ":8443"),
        "accept": ("application/json",),
        "accept-encoding": ("identity",),
        "content-length": ("0",),
    }
    seen = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or len(name) > 32 or len(value) > 128:
            raise ValueError()
        key = name.lower()
        if key in seen or key not in allowed or value not in allowed[key]:
            raise ValueError()
        seen.add(key)
    if "host" not in seen:
        raise ValueError()


def _failure(status: int, code: str) -> RuntimeResponse:
    return RuntimeResponse(status, {"outcome": "denied", "error": {"code": code}})
