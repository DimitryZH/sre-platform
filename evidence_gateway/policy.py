"""Fail-closed policy model for the offline Evidence Gateway B1 slice."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

SCHEMA_VERSION = "evidence-gateway.b1.v1"
OPERATION_COLLECT = "collect_staging_frontend_evidence"

APPROVED_ENVIRONMENT = "staging"
APPROVED_NAMESPACE = "online-shop-stage"
APPROVED_WORKLOAD = "frontend"
APPROVED_SERVICE = "frontend"
APPROVED_ROLLOUT = "frontend"
APPROVED_INGRESS = "online-shop-frontend"
APPROVED_ARGOCD_APPLICATION = "online-shop-stage"

MAX_TIME_RANGE = timedelta(minutes=60)
MAX_AUTH_LIFETIME = timedelta(minutes=10)

EVIDENCE_KINDS = frozenset(
    {
        "kubernetes_state",
        "kubernetes_events",
        "logs",
        "prometheus",
        "deployment_revision",
        "gitops",
    }
)
PROMETHEUS_TEMPLATE_IDS = frozenset({"slo_error_ratio_5m", "slo_burn_rate_5m"})
GITOPS_PATH_IDS = frozenset(
    {
        "stage_argocd_application",
        "stage_values",
        "frontend_rollout",
        "frontend_ingress",
        "frontend_analysis_template",
        "prometheus_rules",
        "burn_rate_alerts",
    }
)

MAX_EVIDENCE_ITEMS = 32
MAX_RESULT_BYTES = 32 * 1024
MAX_LOG_LINES = 80
MAX_LOG_LINE_LENGTH = 240
MAX_LOG_BYTES = 8 * 1024
MAX_GITOPS_FILE_BYTES = 4 * 1024
MAX_EVENT_MESSAGE_LENGTH = 240

SAFE_SUBJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_.:@/-]{2,127}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
UNSAFE_TEXT_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]"),
    re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/-]+=*"),
    re.compile(
        r"\b(127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"
    ),
    re.compile(r"[A-Za-z]:\\"),
)


class EvidenceError(Exception):
    """Deterministic fail-closed error with no backend detail leakage."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EvidenceLimits:
    max_time_range_seconds: int = int(MAX_TIME_RANGE.total_seconds())
    max_evidence_items: int = MAX_EVIDENCE_ITEMS
    max_result_bytes: int = MAX_RESULT_BYTES
    max_log_lines: int = MAX_LOG_LINES
    max_log_line_length: int = MAX_LOG_LINE_LENGTH
    max_log_bytes: int = MAX_LOG_BYTES
    max_gitops_file_bytes: int = MAX_GITOPS_FILE_BYTES
    max_event_message_length: int = MAX_EVENT_MESSAGE_LENGTH


@dataclass(frozen=True)
class TargetPolicy:
    environment: str = APPROVED_ENVIRONMENT
    namespace: str = APPROVED_NAMESPACE
    workload: str = APPROVED_WORKLOAD
    service: str = APPROVED_SERVICE
    rollout: str = APPROVED_ROLLOUT
    ingress: str = APPROVED_INGRESS
    argocd_application: str = APPROVED_ARGOCD_APPLICATION
    pod_label_selector: dict[str, str] = field(
        default_factory=lambda: {"app.kubernetes.io/name": APPROVED_WORKLOAD}
    )
    log_containers: tuple[str, ...] = (APPROVED_WORKLOAD,)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "namespace": self.namespace,
            "workload": self.workload,
            "service": self.service,
            "rollout": self.rollout,
            "ingress": self.ingress,
            "argocd_application": self.argocd_application,
        }

    def validate(self) -> None:
        expected = TargetPolicy().to_public_dict()
        actual = self.to_public_dict()
        if actual != expected:
            raise EvidenceError("out_of_scope", "target policy is outside the approved staging scope")
        if self.pod_label_selector != {"app.kubernetes.io/name": APPROVED_WORKLOAD}:
            raise EvidenceError("ambiguous_target", "pod selector is not the single approved frontend selector")
        if self.log_containers != (APPROVED_WORKLOAD,):
            raise EvidenceError("ambiguous_target", "log container selection is not the single approved container")


@dataclass
class ReplayGuard:
    seen_nonces: set[str] = field(default_factory=set)

    def accept_once(self, nonce: str) -> None:
        if nonce in self.seen_nonces:
            raise EvidenceError("replay_rejected", "request nonce was already used")
        self.seen_nonces.add(nonce)


@dataclass
class EvidencePolicy:
    allowed_subjects: frozenset[str] = frozenset({"ai-operations-staging"})
    required_audience: str = "sre-platform-evidence-gateway"
    required_scope: str = "evidence.read.staging.frontend"
    target: TargetPolicy = field(default_factory=TargetPolicy)
    limits: EvidenceLimits = field(default_factory=EvidenceLimits)
    replay_guard: ReplayGuard = field(default_factory=ReplayGuard)

    def validate_request(self, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        current_time = now or datetime.now(UTC)
        if not isinstance(request, dict):
            raise EvidenceError("malformed_request", "request must be a JSON object")
        allowed_fields = {"schema_version", "operation", "auth", "time_range", "evidence_kinds"}
        unknown = sorted(set(request) - allowed_fields)
        if unknown:
            if any(_looks_like_mutation(value) for value in unknown):
                raise EvidenceError("mutation_intent", "mutation-like request fields are not allowed")
            raise EvidenceError("malformed_request", "request contains unsupported fields")

        if request.get("schema_version") != SCHEMA_VERSION:
            raise EvidenceError("malformed_request", "unsupported schema_version")
        if request.get("operation") != OPERATION_COLLECT:
            raise EvidenceError("unknown_operation", "operation is not approved")

        self.target.validate()
        nonce = self._validate_auth(request.get("auth"), now=current_time)
        start, end = self._validate_time_range(request.get("time_range"))
        kinds = self._validate_evidence_kinds(request.get("evidence_kinds"))
        self.replay_guard.accept_once(nonce)

        return {
            "schema_version": SCHEMA_VERSION,
            "operation": OPERATION_COLLECT,
            "target": self.target.to_public_dict(),
            "time_range": {"start": _format_time(start), "end": _format_time(end)},
            "evidence_kinds": kinds,
            "limits": self.limits.__dict__,
            "prometheus_template_ids": sorted(PROMETHEUS_TEMPLATE_IDS),
            "gitops_path_ids": sorted(GITOPS_PATH_IDS),
        }

    def _validate_auth(self, auth: Any, *, now: datetime) -> str:
        if not isinstance(auth, dict):
            raise EvidenceError("unauthorized", "auth envelope is required")
        allowed = {"subject", "audience", "scope", "issued_at", "expires_at", "nonce"}
        if set(auth) != allowed:
            raise EvidenceError("unauthorized", "auth envelope is incomplete or unsupported")
        subject = auth.get("subject")
        nonce = auth.get("nonce")
        if not isinstance(subject, str) or subject not in self.allowed_subjects or not SAFE_SUBJECT_RE.fullmatch(subject):
            raise EvidenceError("unauthorized", "subject is not authorized")
        if auth.get("audience") != self.required_audience or auth.get("scope") != self.required_scope:
            raise EvidenceError("unauthorized", "audience or scope is not authorized")
        if not isinstance(nonce, str) or not SAFE_ID_RE.fullmatch(nonce):
            raise EvidenceError("malformed_request", "auth nonce is malformed")
        issued_at = _parse_time(auth.get("issued_at"), "auth.issued_at")
        expires_at = _parse_time(auth.get("expires_at"), "auth.expires_at")
        if issued_at > now or expires_at <= now or expires_at <= issued_at:
            raise EvidenceError("stale_request", "auth time bounds are not currently valid")
        if expires_at - issued_at > MAX_AUTH_LIFETIME:
            raise EvidenceError("stale_request", "auth lifetime exceeds the allowed replay window")
        return nonce

    def _validate_time_range(self, value: Any) -> tuple[datetime, datetime]:
        if not isinstance(value, dict) or set(value) != {"start", "end"}:
            raise EvidenceError("invalid_time_range", "time_range must contain only start and end")
        start = _parse_time(value.get("start"), "time_range.start")
        end = _parse_time(value.get("end"), "time_range.end")
        if end <= start:
            raise EvidenceError("invalid_time_range", "time_range end must be after start")
        if end - start > MAX_TIME_RANGE:
            raise EvidenceError("invalid_time_range", "time_range exceeds 60 minutes")
        return start, end

    def _validate_evidence_kinds(self, value: Any) -> list[str]:
        if value is None:
            return sorted(EVIDENCE_KINDS)
        if not isinstance(value, list) or not value:
            raise EvidenceError("malformed_request", "evidence_kinds must be a non-empty list")
        if len(value) != len(set(value)):
            raise EvidenceError("malformed_request", "evidence_kinds must not contain duplicates")
        if any(not isinstance(item, str) for item in value):
            raise EvidenceError("malformed_request", "evidence_kinds entries must be strings")
        unknown = sorted(set(value) - EVIDENCE_KINDS)
        if unknown:
            if any(_looks_like_mutation(item) for item in unknown):
                raise EvidenceError("mutation_intent", "mutation evidence kinds are not allowed")
            raise EvidenceError("unsupported_evidence_kind", "evidence kind is not approved")
        return sorted(value)


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise EvidenceError("malformed_request", f"{field_name} must be an RFC3339 timestamp")
    if contains_unsafe_text(value):
        raise EvidenceError("unsafe_request", "timestamp contains unsafe text")
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EvidenceError("malformed_request", f"{field_name} is malformed") from exc
    if parsed.tzinfo is None:
        raise EvidenceError("malformed_request", f"{field_name} must include timezone")
    return parsed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _looks_like_mutation(value: str) -> bool:
    text = value.lower()
    return any(
        hint in text
        for hint in (
            "write",
            "patch",
            "delete",
            "create",
            "update",
            "apply",
            "sync",
            "promote",
            "abort",
            "rollback",
            "exec",
            "secret",
        )
    )


def contains_unsafe_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in UNSAFE_TEXT_PATTERNS)


def sanitize_string(value: Any, *, max_length: int) -> str:
    if value is None:
        return ""
    text = str(value)
    if contains_unsafe_text(text):
        return "[REDACTED-UNSAFE]"
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > max_length:
        return text[: max_length - 13] + "[TRUNCATED]"
    return text
