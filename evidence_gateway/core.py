"""Typed offline Evidence Gateway implementation for Issue #26 Pass B1."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from .canonical import canonical_json, sha256_hex
from .policy import (
    GITOPS_PATH_IDS,
    PROMETHEUS_TEMPLATE_IDS,
    SCHEMA_VERSION,
    EvidenceError,
    EvidencePolicy,
    contains_unsafe_text,
    sanitize_string,
)
from .providers import EvidenceProviders, ProviderError

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

APPROVED_INGRESS_PATHS = frozenset({"/stage"})
APPROVED_EVENT_OBJECTS = frozenset(
    {
        ("Rollout", "frontend"),
        ("Ingress", "online-shop-frontend"),
    }
)
APPROVED_POD_PHASES = frozenset({"Pending", "Running", "Succeeded", "Failed", "Unknown"})
APPROVED_CONTAINER_STATES = frozenset({"Waiting", "Running", "Terminated"})


class EvidenceGateway:
    def __init__(self, policy: EvidencePolicy, providers: EvidenceProviders) -> None:
        self.policy = policy
        self.providers = providers

    def collect(self, request: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        call_mark = self.providers.mark()
        approved_request: dict[str, Any] | None = None
        request_fingerprint: str | None = None
        try:
            approved_request = self.policy.validate_request_context(request, now=now)
            request_fingerprint = sha256_hex(
                {
                    key: value
                    for key, value in approved_request.items()
                    if key not in {"request_id", "caller_identity"} and not key.startswith("_")
                }
            )
            self.policy.enforce_request_guards(approved_request, now=now)
            result = self._collect_approved(approved_request)
            result_digest = sha256_hex(result)
            source_revision = result.get("source_revision")
            evidence_context = {
                "schema_version": SCHEMA_VERSION,
                "type": "staging_frontend_evidence",
                "target": approved_request["target"],
                "time_range": approved_request["time_range"],
                "source_revision": source_revision,
                "result_digest": result_digest,
            }
            evidence_id = sha256_hex(evidence_context)
            envelope = {
                "schema_version": SCHEMA_VERSION,
                "outcome": "allowed",
                "request_fingerprint": request_fingerprint,
                "evidence_id": evidence_id,
                "target": approved_request["target"],
                "time_range": approved_request["time_range"],
                "limits": approved_request["limits"],
                "result": result,
            }
            result_bytes = len(canonical_json(result).encode("utf-8"))
            envelope["audit"] = self._audit(
                call_mark,
                approved_request=approved_request,
                decision="allowed",
                request_fingerprint=request_fingerprint,
                evidence_id=evidence_id,
                result_digest=result_digest,
                result=result,
                result_bytes=result_bytes,
                revision=source_revision,
            )
            return self._finalize_response(envelope, enforce_max_result_bytes=True)
        except ProviderError:
            return self._denied(
                call_mark,
                "backend_unavailable",
                "approved evidence backend is unavailable",
                approved_request=approved_request,
                request_fingerprint=request_fingerprint,
            )
        except EvidenceError as exc:
            return self._denied(
                call_mark,
                exc.code,
                exc.message,
                approved_request=approved_request,
                request_fingerprint=request_fingerprint,
            )

    def _denied(
        self,
        call_mark: dict[str, int],
        code: str,
        message: str,
        *,
        approved_request: dict[str, Any] | None,
        request_fingerprint: str | None,
    ) -> dict[str, Any]:
        audit = self._audit(
            call_mark,
            approved_request=approved_request,
            decision="denied",
            denial_reason=code,
            request_fingerprint=request_fingerprint,
        )
        return self._finalize_response(
            {
                "schema_version": SCHEMA_VERSION,
                "outcome": "denied",
                "error": {"code": code, "message": message},
                "audit": audit,
            }
        )

    def _finalize_response(
        self, envelope: dict[str, Any], *, enforce_max_result_bytes: bool = False
    ) -> dict[str, Any]:
        audit = envelope["audit"]
        response_bytes = 0
        for _ in range(16):
            audit["response_bytes"] = response_bytes
            actual_bytes = len(canonical_json(envelope).encode("utf-8"))
            if actual_bytes == response_bytes:
                if enforce_max_result_bytes and actual_bytes > self.policy.limits.max_result_bytes:
                    raise EvidenceError("oversized_result", "serialized evidence response exceeds byte limit")
                return envelope
            response_bytes = actual_bytes
        raise EvidenceError("malformed_provider_data", "response size calculation did not converge")

    def _audit(
        self,
        call_mark: dict[str, int],
        *,
        approved_request: dict[str, Any] | None,
        decision: str,
        denial_reason: str | None = None,
        request_fingerprint: str | None = None,
        evidence_id: str | None = None,
        result_digest: str | None = None,
        result: dict[str, Any] | None = None,
        result_bytes: int = 0,
        revision: str | None = None,
    ) -> dict[str, Any]:
        provider_calls = self.providers.call_snapshot_since(call_mark)
        provider_call_params = self.providers.call_records_since(call_mark)
        audit: dict[str, Any] = {
            "decision": decision,
            "denial_reason": denial_reason,
            "provider_calls": provider_calls,
            "provider_call_params": provider_call_params,
            "provider_call_count": sum(provider_calls.values()),
            "result_bytes": result_bytes,
        }
        if approved_request is not None:
            audit.update(
                {
                    "request_id": approved_request["request_id"],
                    "caller_identity": approved_request["caller_identity"],
                    "operation": approved_request["operation"],
                    "target": approved_request["target"],
                    "time_range": approved_request["time_range"],
                }
            )
        if request_fingerprint is not None:
            audit["request_fingerprint"] = request_fingerprint
        if evidence_id is not None:
            audit["evidence_id"] = evidence_id
        if result_digest is not None:
            audit["result_digest"] = result_digest
        if revision is not None:
            audit["revision"] = revision
        if result is not None:
            audit["counts"] = _count_result(result)
        return audit

    def _collect_approved(self, approved_request: dict[str, Any]) -> dict[str, Any]:
        kinds = set(approved_request["evidence_kinds"])
        context = self._context(approved_request)
        sections: list[dict[str, Any]] = []
        deployment_revision = None
        if "kubernetes_state" in kinds:
            sections.append(self._kubernetes_state_section(context))
        if "kubernetes_pod_status" in kinds:
            sections.append(self._kubernetes_pod_status_section(context))
        if "kubernetes_events" in kinds:
            sections.append(self._kubernetes_events_section(context))
        if "logs" in kinds:
            sections.append(self._logs_section(context))
        if "prometheus" in kinds:
            sections.append(self._prometheus_section(context))
        if "deployment_revision" in kinds or "gitops" in kinds:
            deployment_revision = self._deployment_revision(context)
            if "deployment_revision" in kinds:
                sections.append({"kind": "deployment_revision", "data": deployment_revision})
        if "gitops" in kinds:
            if deployment_revision is None:
                deployment_revision = self._deployment_revision(context)
            sections.append(self._gitops_section(context, deployment_revision["revision"]))

        if len(sections) > self.policy.limits.max_evidence_items:
            raise EvidenceError("oversized_result", "evidence item count exceeds limit")
        return {
            "sections": sections,
            "section_count": len(sections),
            "source_revision": deployment_revision["revision"] if deployment_revision else None,
        }

    def _context(self, approved_request: dict[str, Any]) -> dict[str, Any]:
        start = _parse_approved_time(approved_request["time_range"]["start"])
        end = _parse_approved_time(approved_request["time_range"]["end"])
        return {
            "target": approved_request["target"],
            "start": start,
            "end": end,
            "start_text": approved_request["time_range"]["start"],
            "end_text": approved_request["time_range"]["end"],
            "pod_selector": self.policy.target.pod_label_selector,
        }

    def _kubernetes_state_section(self, context: dict[str, Any]) -> dict[str, Any]:
        raw = _expect_mapping(
            self.providers.kubernetes.get_frontend_state(target=context["target"]),
            "kubernetes frontend state",
        )
        workload = self._project_workload(_expect_mapping(raw.get("workload"), "workload state"))
        rollout = self._project_rollout(_expect_mapping(raw.get("rollout"), "rollout state"))
        ingress = self._project_ingress(_expect_mapping(raw.get("ingress"), "ingress state"))
        return {
            "kind": "kubernetes_state",
            "data": {"workload": workload, "rollout": rollout, "ingress": ingress},
        }

    def _kubernetes_events_section(self, context: dict[str, Any]) -> dict[str, Any]:
        raw_events = _expect_sequence(
            self.providers.kubernetes.get_frontend_events(
                target=context["target"],
                start=context["start_text"],
                end=context["end_text"],
                max_items=self.policy.limits.max_events,
            ),
            "events",
        )
        if len(raw_events) > self.policy.limits.max_events:
            raise EvidenceError("oversized_result", "event count exceeds limit")
        events = [
            self._project_event(_expect_mapping(item, "event"), context["start"], context["end"])
            for item in raw_events
        ]
        return {"kind": "kubernetes_events", "data": {"events": events}}

    def _kubernetes_pod_status_section(self, context: dict[str, Any]) -> dict[str, Any]:
        raw_pods = _expect_sequence(
            self.providers.kubernetes.get_frontend_pod_status(
                target=context["target"],
                selector=context["pod_selector"],
                max_pods=self.policy.limits.max_pods,
            ),
            "pod status",
        )
        if len(raw_pods) > self.policy.limits.max_pods:
            raise EvidenceError("oversized_result", "pod status count exceeds limit")
        pods = [self._project_pod_status(_expect_mapping(item, "pod status")) for item in raw_pods]
        return {"kind": "kubernetes_pod_status", "data": {"pods": pods, "pod_count": len(pods)}}

    def _logs_section(self, context: dict[str, Any]) -> dict[str, Any]:
        projected = []
        total_bytes = 0
        raw_lines = _expect_sequence(
            self.providers.logs.get_frontend_container_logs(
                target=context["target"],
                start=context["start_text"],
                end=context["end_text"],
                container=self.policy.target.workload,
                max_lines=self.policy.limits.max_log_lines,
                max_bytes=self.policy.limits.max_log_bytes,
                max_line_length=self.policy.limits.max_log_line_length,
            ),
            "log lines",
        )
        if len(raw_lines) > self.policy.limits.max_log_lines:
            raise EvidenceError("oversized_result", "log line count exceeds limit")
        for item in raw_lines:
            line = self._project_log_line(_expect_mapping(item, "log line"), context["start"], context["end"])
            total_bytes += len(canonical_json(line).encode("utf-8"))
            if total_bytes > self.policy.limits.max_log_bytes:
                raise EvidenceError("oversized_result", "log byte count exceeds limit")
            projected.append(line)
        return {"kind": "logs", "data": {"lines": projected, "line_count": len(projected)}}

    def _prometheus_section(self, context: dict[str, Any]) -> dict[str, Any]:
        series = []
        for template_id in sorted(PROMETHEUS_TEMPLATE_IDS):
            raw_values = _expect_sequence(
                self.providers.prometheus.query_template(
                    template_id,
                    target=context["target"],
                    start=context["start_text"],
                    end=context["end_text"],
                    max_values=self.policy.limits.max_prometheus_values,
                ),
                "prometheus values",
            )
            if len(raw_values) > self.policy.limits.max_prometheus_values:
                raise EvidenceError("oversized_result", "prometheus value count exceeds limit")
            series.append(
                {
                    "template_id": template_id,
                    "expression_name": PROMETHEUS_TEMPLATES[template_id],
                    "values": [
                        self._project_prometheus_value(
                            _expect_mapping(item, "prometheus value"),
                            context["start"],
                            context["end"],
                        )
                        for item in raw_values
                    ],
                }
            )
        return {"kind": "prometheus", "data": {"series": series}}

    def _deployment_revision(self, context: dict[str, Any]) -> dict[str, Any]:
        app = _expect_mapping(
            self.providers.gitops.get_deployment_revision(target=context["target"]),
            "deployment revision",
        )
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

    def _gitops_section(self, context: dict[str, Any], revision: str) -> dict[str, Any]:
        files = []
        for path_id in sorted(GITOPS_PATH_IDS):
            item = _expect_mapping(
                self.providers.gitops.read_file_at_revision(
                    path_id,
                    revision,
                    target=context["target"],
                    max_bytes=self.policy.limits.max_gitops_file_bytes,
                ),
                "gitops file",
            )
            files.append(self._project_gitops_file(path_id, item, revision))
        return {"kind": "gitops", "data": {"revision": revision, "files": files}}

    def _project_workload(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace or raw.get("name") != self.policy.target.workload:
            raise EvidenceError("out_of_scope", "workload state is outside approved target")
        conditions = _expect_sequence(raw.get("conditions", []), "workload conditions")
        if len(conditions) > self.policy.limits.max_conditions:
            raise EvidenceError("oversized_result", "condition count exceeds limit")
        return {
            "name": sanitize_string(raw.get("name"), max_length=128),
            "namespace": sanitize_string(raw.get("namespace"), max_length=128),
            "ready_replicas": _safe_int(raw.get("ready_replicas")),
            "desired_replicas": _safe_int(raw.get("desired_replicas")),
            "available_replicas": _safe_int(raw.get("available_replicas")),
            "conditions": [_project_condition(_expect_mapping(item, "condition")) for item in conditions],
        }

    def _project_rollout(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace or raw.get("name") != self.policy.target.rollout:
            raise EvidenceError("out_of_scope", "rollout state is outside approved target")
        analysis_runs = _expect_sequence(raw.get("analysis_runs", []), "analysis runs")
        if len(analysis_runs) > self.policy.limits.max_analysis_runs:
            raise EvidenceError("oversized_result", "analysis run count exceeds limit")
        return {
            "name": sanitize_string(raw.get("name"), max_length=128),
            "namespace": sanitize_string(raw.get("namespace"), max_length=128),
            "phase": sanitize_string(raw.get("phase"), max_length=64),
            "current_step": _safe_int(raw.get("current_step")),
            "stable_service": self._expect_name(raw.get("stable_service"), self.policy.target.service, "stable service"),
            "canary_service": self._expect_name(raw.get("canary_service"), "frontend-canary", "canary service"),
            "analysis_runs": [
                self._project_analysis_run(_expect_mapping(item, "analysis run")) for item in analysis_runs
            ],
        }

    def _project_analysis_run(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("owner_kind") != "Rollout" or raw.get("owner_name") != self.policy.target.rollout:
            raise EvidenceError("out_of_scope", "analysis run owner is not the approved frontend rollout")
        return {
            "name": sanitize_string(raw.get("name"), max_length=128),
            "phase": sanitize_string(raw.get("phase"), max_length=64),
            "owner_kind": "Rollout",
            "owner_name": self.policy.target.rollout,
        }

    def _project_ingress(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace or raw.get("name") != self.policy.target.ingress:
            raise EvidenceError("out_of_scope", "ingress state is outside approved target")
        paths = _expect_sequence(raw.get("paths", []), "ingress paths")
        if any(not isinstance(path, str) for path in paths):
            raise EvidenceError("malformed_provider_data", "ingress paths are malformed")
        if any(path not in APPROVED_INGRESS_PATHS for path in paths):
            raise EvidenceError("out_of_scope", "ingress path is outside approved stage path")
        return {
            "name": sanitize_string(raw.get("name"), max_length=128),
            "namespace": sanitize_string(raw.get("namespace"), max_length=128),
            "class_name": sanitize_string(raw.get("class_name"), max_length=64),
            "paths": list(paths),
        }

    def _project_pod_status(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace:
            raise EvidenceError("out_of_scope", "pod status is outside approved namespace")
        if raw.get("workload") != self.policy.target.workload or raw.get("container") != self.policy.target.workload:
            raise EvidenceError("out_of_scope", "pod status is outside approved frontend workload")
        phase = raw.get("phase")
        container_state = raw.get("container_state")
        if not isinstance(phase, str) or phase not in APPROVED_POD_PHASES:
            raise EvidenceError("malformed_provider_data", "pod phase is malformed")
        if not isinstance(container_state, str) or container_state not in APPROVED_CONTAINER_STATES:
            raise EvidenceError("malformed_provider_data", "container state is malformed")
        return {
            "pod_id": sanitize_string(raw.get("pod_id"), max_length=128),
            "phase": phase,
            "ready": _safe_bool(raw.get("ready")),
            "restart_count": _safe_int(raw.get("restart_count")),
            "container_state": container_state,
        }

    def _project_event(self, raw: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace:
            raise EvidenceError("out_of_scope", "event is outside approved namespace")
        involved_kind = raw.get("involved_kind")
        involved_name = raw.get("involved_name")
        if (involved_kind, involved_name) not in APPROVED_EVENT_OBJECTS:
            raise EvidenceError("out_of_scope", "event is outside approved involved object set")
        timestamp = _safe_timestamp(raw.get("timestamp"), start=start, end=end)
        return {
            "timestamp": timestamp,
            "type": sanitize_string(raw.get("type"), max_length=32),
            "reason": sanitize_string(raw.get("reason"), max_length=64),
            "message": sanitize_string(raw.get("message"), max_length=self.policy.limits.max_event_message_length),
            "involved_kind": involved_kind,
            "involved_name": involved_name,
        }

    def _project_log_line(self, raw: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
        if raw.get("namespace") != self.policy.target.namespace:
            raise EvidenceError("out_of_scope", "log line is outside approved namespace")
        if raw.get("workload") != self.policy.target.workload or raw.get("container") != self.policy.target.workload:
            raise EvidenceError("out_of_scope", "log line is outside approved frontend container")
        message = sanitize_string(raw.get("message"), max_length=self.policy.limits.max_log_line_length)
        return {
            "timestamp": _safe_timestamp(raw.get("timestamp"), start=start, end=end),
            "pod": sanitize_string(raw.get("pod"), max_length=128),
            "container": self.policy.target.workload,
            "message": message,
        }

    def _project_prometheus_value(self, raw: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
        labels = _expect_mapping(raw.get("labels"), "prometheus labels")
        namespace = labels.get("exported_namespace") or labels.get("namespace")
        if namespace not in {None, self.policy.target.namespace}:
            raise EvidenceError("out_of_scope", "prometheus value is outside approved namespace")
        if labels.get("namespace") not in {None, self.policy.target.namespace}:
            raise EvidenceError("out_of_scope", "prometheus value has conflicting namespace label")
        if labels.get("service") not in {None, self.policy.target.service}:
            raise EvidenceError("out_of_scope", "prometheus value has conflicting service label")
        if labels.get("ingress") not in {None, self.policy.target.ingress}:
            raise EvidenceError("out_of_scope", "prometheus value has conflicting ingress label")
        return {
            "timestamp": _safe_timestamp(raw.get("timestamp"), start=start, end=end),
            "value": _safe_float(raw.get("value")),
            "labels": {
                key: sanitize_string(labels[key], max_length=80)
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


def _safe_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise EvidenceError("malformed_provider_data", "boolean provider value is malformed")
    return value


def _safe_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("malformed_provider_data", "numeric provider value is malformed")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise EvidenceError("malformed_provider_data", "numeric provider value must be finite")
    return numeric


def _safe_timestamp(value: Any, *, start: datetime, end: datetime) -> str:
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
    timestamp = parsed.astimezone(UTC)
    if timestamp < start or timestamp > end:
        raise EvidenceError("outside_time_range", "provider evidence timestamp is outside the approved time range")
    return timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_approved_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("malformed_provider_data", f"{label} must be an object")
    return value


def _expect_sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError("malformed_provider_data", f"{label} must be an array")
    return value


def _count_result(result: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {"sections": len(result.get("sections", []))}
    for section in result.get("sections", []):
        kind = section.get("kind")
        data = section.get("data", {})
        if kind == "kubernetes_events":
            counts["events"] = len(data.get("events", []))
        elif kind == "kubernetes_pod_status":
            counts["pods"] = len(data.get("pods", []))
        elif kind == "logs":
            counts["log_lines"] = len(data.get("lines", []))
        elif kind == "prometheus":
            counts["prometheus_values"] = sum(len(series.get("values", [])) for series in data.get("series", []))
        elif kind == "gitops":
            counts["gitops_files"] = len(data.get("files", []))
    return counts
