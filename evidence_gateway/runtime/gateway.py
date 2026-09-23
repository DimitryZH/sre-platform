"""Private HTTP request boundary for the repository-only runtime foundation."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Mapping, Protocol

from ..canonical import canonical_json
from ..core import EvidenceGateway
from ..policy import EVIDENCE_KINDS, EvidenceError, EvidencePolicy
from ..providers import EvidenceProviders
from .state import RuntimeState, RuntimeStateError

HTTP_OPERATION_PATH = "/v1/evidence/staging/frontend"
EXTERNAL_TOKEN_AUDIENCE = "sre-platform-evidence-gateway"
KUBERNETES_API_AUDIENCE = "https://kubernetes.default.svc"
VALIDATION_KUBERNETES_SUBJECT = "system:serviceaccount:evidence-gateway-validation:staging-evidence-client"
POLICY_SUBJECT = "staging-evidence-client"
MAX_HTTP_REQUEST_BYTES = 8 * 1024
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


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


class RuntimeEvidencePolicy(EvidencePolicy):
    """B1 schema and target validation after the runtime reserves durable state."""

    def __init__(self) -> None:
        super().__init__(allowed_subjects=frozenset({POLICY_SUBJECT}))

    def enforce_request_guards(self, approved_request: dict[str, Any], *, now: datetime | None = None) -> None:
        # RuntimeGateway reserves the nonce and rate slot before parsing the body.
        return None


class RuntimeGateway:
    """A single private POST operation with server-derived authorization context."""

    def __init__(
        self,
        *,
        token_reviewer: TokenReviewClient,
        providers: EvidenceProviders,
        state: RuntimeState,
        runtime_token: RuntimeTokenConfig = RuntimeTokenConfig(),
        policy: EvidencePolicy | None = None,
    ) -> None:
        self.token_reviewer = token_reviewer
        self.state = state
        self.runtime_token = runtime_token
        self.evidence = EvidenceGateway(policy or RuntimeEvidencePolicy(), providers)

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
        token = _bearer_token(headers)
        if token is None:
            return _failure(401, "unauthorized")
        identity = self._authenticate(token)
        if identity is None:
            return _failure(401, "unauthorized")

        current = now or datetime.now(UTC)
        nonce = _header(headers, "x-request-nonce")
        nonce_hash = sha256((nonce if isinstance(nonce, str) else "").encode("utf-8")).hexdigest()
        nonce_is_valid = _decode_nonce(nonce) is not None
        try:
            with self.state.request_transaction(
                subject=identity,
                nonce_hash=nonce_hash,
                now=current,
                expires_at=current + timedelta(minutes=10),
            ) as transaction:
                audit = _base_audit(current, identity, nonce_hash)
                response = self._collect_or_deny(
                    transaction.denial,
                    nonce_is_valid=nonce_is_valid,
                    nonce=nonce,
                    headers=headers,
                    body=body,
                    now=current,
                    audit=audit,
                )
                response_audit = response["audit"]
                response_audit.update(audit)
                self.evidence._finalize_response(
                    response,
                    enforce_max_result_bytes=response.get("outcome") == "allowed",
                )
                transaction.write_audit(response_audit, now=current)
        except (EvidenceError, RuntimeStateError):
            return _failure(503, "audit_unavailable")
        return RuntimeResponse(status=_status_for(response), body=response)

    def _collect_or_deny(
        self,
        guard_denial: EvidenceError | None,
        *,
        nonce_is_valid: bool,
        nonce: str | None,
        headers: Mapping[str, str],
        body: bytes,
        now: datetime,
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        if guard_denial is not None:
            return _denied(guard_denial.code, audit)
        if not nonce_is_valid:
            return _denied("invalid_nonce", audit)
        if _header(headers, "content-type") != "application/json" or not isinstance(body, bytes) or len(body) > MAX_HTTP_REQUEST_BYTES:
            return _denied("malformed_request", audit)
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _denied("malformed_request", audit)
        if not isinstance(request, dict):
            return _denied("malformed_request", audit)
        audit["evidence_kinds"] = _safe_evidence_kinds(request.get("evidence_kinds"))
        if "auth" in request:
            return _denied("body_auth_forbidden", audit)
        request_with_auth = dict(request)
        request_with_auth["auth"] = {
            "subject": POLICY_SUBJECT,
            "audience": EXTERNAL_TOKEN_AUDIENCE,
            "scope": "evidence.read.staging.frontend",
            "issued_at": _format(now),
            "expires_at": _format(now + timedelta(minutes=10)),
            "nonce": nonce,
        }
        return self.evidence.collect(request_with_auth, now=now)

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


def _base_audit(now: datetime, identity: str, nonce_hash: str) -> dict[str, Any]:
    return {
        "event_time": _format(now),
        "kubernetes_subject": identity,
        "rate_subject": identity,
        "policy_subject": POLICY_SUBJECT,
        "authentication_method": "kubernetes_tokenreview",
        "token_audience": EXTERNAL_TOKEN_AUDIENCE,
        "nonce_sha256": nonce_hash,
    }


def _denied(code: str, audit: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "schema_version": "evidence-gateway.b1.v1",
        "outcome": "denied",
        "error": {"code": code},
        "audit": {
            "decision": "denied",
            "denial_reason": code,
            "provider_calls": {},
            "provider_call_count": 0,
            "result_bytes": 0,
            **audit,
        },
    }
    response_bytes = 0
    for _ in range(16):
        envelope["audit"]["response_bytes"] = response_bytes
        actual = len(canonical_json(envelope).encode("utf-8"))
        if actual == response_bytes:
            return envelope
        response_bytes = actual
    raise RuntimeStateError()


def _safe_evidence_kinds(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in EVIDENCE_KINDS for item in value):
        return []
    return sorted(set(value))


def _decode_nonce(value: Any) -> bytes | None:
    if not isinstance(value, str) or not NONCE_RE.fullmatch(value):
        return None
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None
    if len(decoded) != 32:
        return None
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    return decoded if canonical == value else None


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
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class PrivateHTTPApplication:
    """Small WSGI boundary; network exposure and listener placement stay external."""

    def __init__(self, gateway: RuntimeGateway) -> None:
        self.gateway = gateway

    def __call__(self, environ: Mapping[str, Any], start_response: Any) -> list[bytes]:
        try:
            content_length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            content_length = MAX_HTTP_REQUEST_BYTES + 1
        stream = environ.get("wsgi.input")
        body = b"\x00" * (MAX_HTTP_REQUEST_BYTES + 1) if content_length < 0 or content_length > MAX_HTTP_REQUEST_BYTES else (
            stream.read(content_length) if stream is not None else b""
        )
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
