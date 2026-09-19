"""Typed offline Evidence Gateway implementation for Issue #26 Pass B1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .canonical import canonical_json, sha256_hex
from .policy import (
    GITOPS_PATH_IDS,
    MAX_RESULT_BYTES,
    PROMETHEUS_TEMPLATE_IDS,
    SCHEMA_VERSION,
    EvidenceError,
    EvidencePolicy,
    contains_unsafe_text,
    sanitize_string,
)
from .providers import EvidenceProviders

GITOPS_PATHS = {
    "stage_argocd_application": "environments/stage/argocd/apps/online-shop-stage.yaml",
    "stage_values": "environments/stage/values/platform.yaml",
    "frontend_rollout": "charts/platform/templates/frontend-rollout.yaml",
    "frontend_ingress": "charts/platform/templates/frontend-ingress.yaml",
    "frontend_analysis_template": "charts/platform/templates/frontend-slo-check-analysis-template.yaml",
    "prometheus_rules": "charts/platform/templates/prometheus-rules.yaml",
    "burn_rate_alerts": "charts/platform/templates/burn-rate-alerts.yaml",
}

PROMETHEUS_TEMPLATES = {
    "slo_error_ratio_5m": "slo:error_ratio_5m",
    "slo_burn_rate_5m": "slo:burn_rate_5m",
}


class EvidenceGateway:
    def __init__(self, policy: EvidencePolicy, providers: EvidenceProviders) -> None:
        self.policy = policy
        self.providers = providers

    def collect(self, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        try:
            approved_request = self.policy.validate_request(request, now=now)
            request_fingerprint = sha256_hex(approved_request)
            result = self._collect_approved(approved_request)
            result_bytes = len(canonical_json(result).encode("utf-8"))
            if result_bytes > self.policy.limits.max_result_bytes:
                raise EvidenceError("oversized_result", "sanitized evidence result exceeds byte limit")
            evidence_id = sha256_hex(result)
            return {
                "schema_version": SCHEMA_VERSION,
                "outcome": "allowed",
                "request_fingerprint": request_fingerprint,
                "evidence_id": evidence_id,
                "target": approved_request["target"],
                "time_range": approved_request["time_range"],
                "limits": approved_request["limits"],
                "result": result,
                "audit": {
                    "decision": "allowed",
                    "provider_calls": self.providers.call_snapshot(),
                    "result_bytes": result_bytes,
                },
            }
        except EvidenceError as exc:
            return {
                "schema_version": SCHEMA_VERSION,
                "outcome": "denied",
                "error": {"code": exc.code, "message": exc.message},
                "audit": {
                    "decision": "denied",
                    "provider_calls": self.providers.call_snapshot(),
                },
            }

    def _collect_approved(self, approved_request: dict[str, Any]) -> dict[str, Any]:
        kinds = set(approved_request["evidence_kinds"])
        sections: list[dict[str, Any]] = []
        if {"kubernetes_state", "kubernetes_events"} & kinds:
            if "kubernetes_state" in kinds:
                sections.append(self._kubernetes_state_section())
            if "kubernetes_events" in kinds:
                sections.append(self._kubernetes_events_section())
        if "logs" in kinds:
            sections.append(self._logs_section())
        if "prometheus" in kinds:
            sections.append(self._prometheus_section())
        deployment_revision = None
        if "deployment_revision" in kinds or "gitops" in kinds:
            deployment_revision = self._deployment_revision()
            if "deployment_revision" in kinds:
                sections.append({"kind": "deployment_revision", "data": deployment_revision})
        if "gitops" in kinds:
            if deployment_revision is None:
                deployment_revision = self._deployment_revision()
            sections.append(self._gitops_section(deployment_revision["revision"]))

        if len(sections) > self.policy.limits.max_evidence_items:
            raise EvidenceError("oversized_result", "evidence item count exceeds limit")
        return {"sections": sections, "section_count": len(sections)}

    def _kubernetes_state_section(self) -> dict[str, Any]:
        raw = self.providers.kubernetes.get_frontend_state()
        workload = self._project_workload(raw.get("workload", {}))
        rollout = self._project_rollout(raw.get("rollout", {}))
        ingress = self._project_ingress(raw.get("ingress", {}))
        return {
            "kind": "kubernetes_state",
            "data": {"workload": workload, "rollout": rollout, "ingress": ingress},
        }

    def _kubernetes_events_section(self) -> dict[str, Any]:
        events = [self._project_event(item) for item in self.providers.kubernetes.get_frontend_events()]
        if len(events) > self.policy.limits.max_evidence_items:
            raise EvidenceError("oversized_result", "event count exceeds limit")
        return {"kind": "kubernetes_events", "data": {"events": events}}

    def _logs_section(self) -> dict[str, Any]:
        projected = []
        total_bytes = 0
        raw_lines = self.providers.logs.get_frontend_container_logs()
        if len(raw_lines) > self.policy.limits.max_log_lines:
            raise EvidenceError("oversized_result", "log line count exceeds limit")
        for item in raw_lines:
            line = self._project_log_line(item)
            total_bytes += len(canonical_json(line).encode("utf-8"))
            if total_bytes > self.policy.limits.max_log_bytes:
                raise EvidenceError("oversized_result", "log byte count exceeds limit")
            projected.append(line)
        return {"kind": "logs", "data": {"lines": projected, "line_count": len(projected)}}

    def _prometheus_section(self) -> dict[str, Any]:
        series = []
        for template_id in sorted(PROMETHEUS_TEMPLATE_IDS):
            values = self.providers.prometheus.query_template(template_id)
            series.append(
                {
                    "template_id": template_id,
                    "expression_name": PROMETHEUS_TEMPLATES[template_id],
                    "values": [self._project_prometheus_value(item) for item in values],
                }
            )
        return {"kind": "prometheus", "data": {"series": series}}

    def _deployment_revision(self) -> dict[str, Any]:
        app = self.providers.gitops.get_deployment_revision()
        name = app.get("application")
        revision = app.get("revision")
        if name != self.policy.target.argocd_application:
            raise EvidenceError("out_of_scope", "deployment revision is not for the approved application")
        if not isinstance(revision, str) or len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
            raise EvidenceError("malformed_provider_data", "deployment revision is not an immutable commit SHA")
        return {
            "application": name,
            "revision": revision,
            "sync_status": sanitize_string(app.get("sync_status"), max_length=32),
            "health_status": sanitize_string(app.get("health_status"), max_length=32),
        }

    def _gitops_section(self, revision: str) -> dict[str, Any]:
        files = []
        for path_id in sorted(GITOPS_PATH_IDS):
            item = self.providers.gitops.read_file_at_revision(path_id, revision)
            files.append(self._project_gitops_file(path_id, item, revision))
        return {"kind": "gitops", "data": {"revision": revision, "files": files}}

    def _project_workload(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace or raw.get("name") != self.policy.target.workload:
            raise EvidenceError("out_of_scope", "workload state is outside approved target")
        return {
            "name": raw["name"],
            "namespace": raw["namespace"],
            "ready_replicas": _safe_int(raw.get("ready_replicas")),
            "desired_replicas": _safe_int(raw.get("desired_replicas")),
            "available_replicas": _safe_int(raw.get("available_replicas")),
            "conditions": [_project_condition(item) for item in raw.get("conditions", [])],
        }

    def _project_rollout(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace or raw.get("name") != self.policy.target.rollout:
            raise EvidenceError("out_of_scope", "rollout state is outside approved target")
        return {
            "name": raw["name"],
            "namespace": raw["namespace"],
            "phase": sanitize_string(raw.get("phase"), max_length=64),
            "current_step": _safe_int(raw.get("current_step")),
            "stable_service": self._expect_name(raw.get("stable_service"), self.policy.target.service, "stable service"),
            "canary_service": self._expect_name(raw.get("canary_service"), "frontend-canary", "canary service"),
            "analysis_runs": [
                {
                    "name": sanitize_string(item.get("name"), max_length=128),
                    "phase": sanitize_string(item.get("phase"), max_length=64),
                }
                for item in raw.get("analysis_runs", [])
            ],
        }

    def _project_ingress(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace or raw.get("name") != self.policy.target.ingress:
            raise EvidenceError("out_of_scope", "ingress state is outside approved target")
        paths = raw.get("paths", [])
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise EvidenceError("malformed_provider_data", "ingress paths are malformed")
        allowed_paths = [path for path in paths if path.startswith("/stage")]
        if len(allowed_paths) != len(paths):
            raise EvidenceError("out_of_scope", "ingress path is outside approved stage prefix")
        return {
            "name": raw["name"],
            "namespace": raw["namespace"],
            "class_name": sanitize_string(raw.get("class_name"), max_length=64),
            "paths": allowed_paths,
        }

    def _project_event(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace:
            raise EvidenceError("out_of_scope", "event is outside approved namespace")
        involved_name = raw.get("involved_name")
        if involved_name not in {self.policy.target.workload, self.policy.target.rollout, self.policy.target.ingress}:
            raise EvidenceError("out_of_scope", "event is outside approved involved object set")
        return {
            "timestamp": _safe_timestamp(raw.get("timestamp")),
            "type": sanitize_string(raw.get("type"), max_length=32),
            "reason": sanitize_string(raw.get("reason"), max_length=64),
            "message": sanitize_string(raw.get("message"), max_length=self.policy.limits.max_event_message_length),
            "involved_kind": sanitize_string(raw.get("involved_kind"), max_length=64),
            "involved_name": involved_name,
        }

    def _project_log_line(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace:
            raise EvidenceError("out_of_scope", "log line is outside approved namespace")
        if raw.get("workload") != self.policy.target.workload or raw.get("container") != self.policy.target.workload:
            raise EvidenceError("out_of_scope", "log line is outside approved frontend container")
        message = sanitize_string(raw.get("message"), max_length=self.policy.limits.max_log_line_length)
        return {
            "timestamp": _safe_timestamp(raw.get("timestamp")),
            "pod": sanitize_string(raw.get("pod"), max_length=128),
            "container": self.policy.target.workload,
            "message": message,
        }

    def _project_prometheus_value(self, raw: dict[str, Any]) -> dict[str, Any]:
        labels = raw.get("labels", {})
        if not isinstance(labels, dict):
            raise EvidenceError("malformed_provider_data", "prometheus labels are malformed")
        namespace = labels.get("exported_namespace") or labels.get("namespace")
        if namespace not in {None, self.policy.target.namespace}:
            raise EvidenceError("out_of_scope", "prometheus value is outside approved namespace")
        return {
            "timestamp": _safe_timestamp(raw.get("timestamp")),
            "value": _safe_float(raw.get("value")),
            "labels": {
                key: sanitize_string(labels.get(key), max_length=80)
                for key in sorted(set(labels) & {"exported_namespace", "namespace", "service", "ingress"})
            },
        }

    def _project_gitops_file(self, path_id: str, raw: dict[str, Any], revision: str) -> dict[str, Any]:
        if raw.get("revision") != revision:
            raise EvidenceError("malformed_provider_data", "gitops file revision does not match deployment revision")
        if path_id not in GITOPS_PATHS or raw.get("path") != GITOPS_PATHS[path_id]:
            raise EvidenceError("out_of_scope", "gitops path is outside approved path IDs")
        content = raw.get("content", "")
        if not isinstance(content, str):
            raise EvidenceError("malformed_provider_data", "gitops content is malformed")
        if len(content.encode("utf-8")) > self.policy.limits.max_gitops_file_bytes:
            raise EvidenceError("oversized_result", "gitops file exceeds byte limit")
        if contains_unsafe_text(content):
            raise EvidenceError("unsafe_provider_output", "gitops content contains unsafe text")
        return {
            "path_id": path_id,
            "path": GITOPS_PATHS[path_id],
            "revision": revision,
            "content_sha256": sha256_hex({"content": content}),
            "content": content,
        }

    @staticmethod
    def _expect_name(value: Any, expected: str, field_name: str) -> str:
        if value != expected:
            raise EvidenceError("out_of_scope", f"{field_name} is outside approved target")
        return expected


def _project_condition(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": sanitize_string(raw.get("type"), max_length=64),
        "status": sanitize_string(raw.get("status"), max_length=32),
        "reason": sanitize_string(raw.get("reason"), max_length=128),
    }


def _safe_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1000:
        raise EvidenceError("malformed_provider_data", "integer provider value is malformed")
    return value


def _safe_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("malformed_provider_data", "numeric provider value is malformed")
    return float(value)


def _safe_timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise EvidenceError("malformed_provider_data", "timestamp provider value is malformed")
    if contains_unsafe_text(value):
        raise EvidenceError("unsafe_provider_output", "timestamp provider value is unsafe")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("malformed_provider_data", "timestamp provider value is malformed") from exc
    if parsed.tzinfo is None:
        raise EvidenceError("malformed_provider_data", "timestamp provider value must include timezone")
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
