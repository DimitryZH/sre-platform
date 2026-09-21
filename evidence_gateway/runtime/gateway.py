"""Private HTTP request boundary for the repository-only runtime foundation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Mapping, Protocol

from ..canonical import canonical_json
from ..core import EvidenceGateway
from ..policy import EvidenceError, EvidencePolicy, SAFE_ID_RE
from ..providers import EvidenceProviders
from .state import RuntimeState, RuntimeStateError

HTTP_OPERATION_PATH = "/v1/evidence/staging/frontend"
EXTERNAL_TOKEN_AUDIENCE = "sre-platform-evidence-gateway"
KUBERNETES_API_AUDIENCE = "https://kubernetes.default.svc"
VALIDATION_KUBERNETES_SUBJECT = "system:serviceaccount:evidence-gateway-validation:staging-evidence-client"
POLICY_SUBJECT = "staging-evidence-client"
MAX_HTTP_REQUEST_BYTES = 8 * 1024


@dataclass(frozen=True)
class RuntimeTokenConfig:
    """The only projected token the runtime needs for Kubernetes API access."""

    path: str = "/var/run/secrets/evidence-gateway/kubernetes/token"
    audience: str = KUBERNETES_API_AUDIENCE


@dataclass(frozen=True)
class TokenReviewResult:
    authenticated: bool
    username: str | None
    audiences: frozenset[str]


class TokenReviewClient(Protocol):
    def review(self, *, token: str, audience: str) -> TokenReviewResult: ...


@dataclass(frozen=True)
class RuntimeResponse:
    status: int
    body: dict[str, Any]

    def json_bytes(self) -> bytes:
        return canonical_json(self.body).encode("utf-8")


class DurableEvidencePolicy(EvidencePolicy):
    """B1 validation plus SQLite-backed replay and rate enforcement."""

    def __init__(self, state: RuntimeState) -> None:
        super().__init__(allowed_subjects=frozenset({POLICY_SUBJECT}))
        self.state = state

    def enforce_request_guards(self, approved_request: dict[str, Any], *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        self.state.check_and_record(
            subject=approved_request["caller_identity"],
            nonce=approved_request["_replay_nonce"],
            now=current,
            expires_at=approved_request["_auth_expires_at"],
        )


class RuntimeGateway:
    """A single private POST operation with server-derived authorization context."""

    def __init__(
        self,
        *,
        token_reviewer: TokenReviewClient,
        providers: EvidenceProviders,
        state: RuntimeState,
        runtime_token: RuntimeTokenConfig = RuntimeTokenConfig(),
    ) -> None:
        self.token_reviewer = token_reviewer
        self.state = state
        self.runtime_token = runtime_token
        self.evidence = EvidenceGateway(DurableEvidencePolicy(state), providers)

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        now: datetime | None = None,
    ) -> RuntimeResponse:
        if method != "POST" or path != HTTP_OPERATION_PATH:
            return _failure(404, "unknown_operation")
        if not isinstance(body, bytes) or len(body) > MAX_HTTP_REQUEST_BYTES:
            return _failure(400, "malformed_request")
        current = now or datetime.now(UTC)
        token = _bearer_token(headers)
        nonce = _header(headers, "x-request-nonce")
        if _header(headers, "content-type") != "application/json":
            return _failure(400, "malformed_request")
        if token is None or not isinstance(nonce, str) or not SAFE_ID_RE.fullmatch(nonce):
            return _failure(401, "unauthorized")
        identity = self._authenticate(token)
        if identity is None:
            return _failure(401, "unauthorized")
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _failure(400, "malformed_request")
        if not isinstance(request, dict):
            return _failure(400, "malformed_request")
        if "auth" in request:
            return _failure(400, "body_auth_forbidden")
        request_with_auth = dict(request)
        request_with_auth["auth"] = {
            "subject": POLICY_SUBJECT,
            "audience": EXTERNAL_TOKEN_AUDIENCE,
            "scope": "evidence.read.staging.frontend",
            "issued_at": _format(current),
            "expires_at": _format(current + timedelta(minutes=10)),
            "nonce": nonce,
        }
        response = self.evidence.collect(request_with_auth, now=current)
        audit = response.get("audit")
        if isinstance(audit, dict):
            audit.update(
                {
                    "kubernetes_subject": identity,
                    "policy_subject": POLICY_SUBJECT,
                    "authentication_method": "kubernetes_tokenreview",
                    "token_audience": EXTERNAL_TOKEN_AUDIENCE,
                    "nonce_sha256": sha256(nonce.encode("utf-8")).hexdigest(),
                }
            )
            try:
                self.evidence._finalize_response(
                    response,
                    enforce_max_result_bytes=response.get("outcome") == "allowed",
                )
                self.state.write_audit(audit, now=current)
            except (EvidenceError, RuntimeStateError):
                return _failure(503, "audit_unavailable")
        return RuntimeResponse(status=_status_for(response), body=response)

    def _authenticate(self, token: str) -> str | None:
        try:
            reviewed = self.token_reviewer.review(token=token, audience=EXTERNAL_TOKEN_AUDIENCE)
        except Exception:
            return None
        if not isinstance(reviewed, TokenReviewResult) or not reviewed.authenticated:
            return None
        if reviewed.username != VALIDATION_KUBERNETES_SUBJECT or reviewed.audiences != frozenset({EXTERNAL_TOKEN_AUDIENCE}):
            return None
        return reviewed.username


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _bearer_token(headers: Mapping[str, str]) -> str | None:
    value = _header(headers, "authorization")
    if not isinstance(value, str) or not value.startswith("Bearer "):
        return None
    token = value.removeprefix("Bearer ")
    return token if token else None


def _failure(status: int, code: str) -> RuntimeResponse:
    return RuntimeResponse(
        status=status,
        body={"schema_version": "evidence-gateway.b1.v1", "outcome": "denied", "error": {"code": code}},
    )


def _status_for(response: dict[str, Any]) -> int:
    if response.get("outcome") == "allowed":
        return 200
    code = response.get("error", {}).get("code")
    if code in {"replay_rejected", "rate_limited", "replay_capacity_exceeded", "rate_limit_unavailable"}:
        return 429
    if code in {"backend_unavailable", "audit_unavailable", "state_capacity_exceeded"}:
        return 503
    return 400


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class PrivateHTTPApplication:
    """Small WSGI boundary; network exposure and listener placement stay external."""

    def __init__(self, gateway: RuntimeGateway) -> None:
        self.gateway = gateway

    def __call__(self, environ: Mapping[str, Any], start_response: Any) -> list[bytes]:
        try:
            content_length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            content_length = MAX_HTTP_REQUEST_BYTES + 1
        if content_length < 0 or content_length > MAX_HTTP_REQUEST_BYTES:
            response = _failure(400, "malformed_request")
        else:
            stream = environ.get("wsgi.input")
            body = stream.read(content_length) if stream is not None else b""
            headers = {
                key[5:].replace("_", "-"): value
                for key, value in environ.items()
                if key.startswith("HTTP_") and isinstance(value, str)
            }
            if isinstance(environ.get("CONTENT_TYPE"), str):
                headers["Content-Type"] = environ["CONTENT_TYPE"]
            response = self.gateway.handle(
                method=environ.get("REQUEST_METHOD", ""),
                path=environ.get("PATH_INFO", ""),
                headers=headers,
                body=body,
            )
        payload = response.json_bytes()
        start_response(
            f"{response.status} {_reason(response.status)}",
            [("Content-Type", "application/json"), ("Content-Length", str(len(payload)))],
        )
        return [payload]


def _reason(status: int) -> str:
    return {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 429: "Too Many Requests", 503: "Service Unavailable"}[status]
