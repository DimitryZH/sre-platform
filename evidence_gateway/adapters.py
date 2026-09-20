"""Offline-contract provider adapters for the bounded evidence gateway.

The adapters expose the same typed provider methods as the B1 fakes. Their
backends are injected protocols used only by local tests in this repository;
they do not create clients, read credentials, or perform network I/O.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, TypedDict

from .policy import (
    APPROVED_ARGOCD_APPLICATION,
    APPROVED_INGRESS,
    APPROVED_NAMESPACE,
    APPROVED_WORKLOAD,
    MAX_ANALYSIS_RUNS,
    MAX_CONDITIONS,
    MAX_EVENTS,
    MAX_GITOPS_FILE_BYTES,
    MAX_LOG_BYTES,
    MAX_LOG_LINE_LENGTH,
    MAX_LOG_LINES,
    MAX_PODS,
    MAX_PROMETHEUS_VALUES,
    SHA_RE,
    TargetPolicy,
)
from .providers import ProviderCalls, ProviderError

FRONTEND_SELECTOR = {"app.kubernetes.io/name": APPROVED_WORKLOAD}
EVENT_OBJECTS = (
    ("Rollout", APPROVED_WORKLOAD),
    ("Ingress", APPROVED_INGRESS),
)
PROMETHEUS_RECORDING_RULES = {
    "slo_error_ratio_5m": "slo:error_ratio_5m",
    "slo_burn_rate_5m": "slo:burn_rate_5m",
}
GITOPS_PATHS = {
    "stage_argocd_application": "environments/stage/argocd/apps/online-shop-stage.yaml",
    "stage_values": "environments/stage/values/platform.yaml",
    "frontend_rollout": "charts/platform/templates/frontend-rollout.yaml",
    "frontend_ingress": "charts/platform/templates/frontend-ingress.yaml",
    "frontend_analysis_template": "charts/platform/templates/frontend-slo-check-analysis-template.yaml",
    "prometheus_rules": "charts/platform/templates/prometheus-rules.yaml",
    "burn_rate_alerts": "charts/platform/templates/burn-rate-alerts.yaml",
}
APPROVED_TARGET = TargetPolicy().to_public_dict()


class RolloutBackendRecord(TypedDict):
    """Normalized narrow Rollout response used to derive B1 state evidence."""

    name: str
    namespace: str
    ready_replicas: int
    desired_replicas: int
    available_replicas: int
    conditions: list[dict[str, Any]]
    phase: str
    current_step: int
    stable_service: str
    canary_service: str
    analysis_runs: list[dict[str, Any]]


class KubernetesBackend(Protocol):
    def get_rollout(self, *, namespace: str, name: str) -> RolloutBackendRecord: ...

    def get_ingress(self, *, namespace: str, name: str) -> dict[str, Any]: ...

    def list_pods(self, *, namespace: str, label_selector: dict[str, str], limit: int) -> list[dict[str, Any]]: ...

    def list_events(
        self,
        *,
        namespace: str,
        involved_kind: str,
        involved_name: str,
        start: str,
        end: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...


class FrontendLogBackend(Protocol):
    def read_frontend_container_logs(
        self,
        *,
        namespace: str,
        workload: str,
        container: str,
        start: str,
        end: str,
        max_lines: int,
        max_bytes: int,
        max_line_length: int,
    ) -> list[dict[str, Any]]: ...


class PrometheusBackend(Protocol):
    def query_recording_rule(
        self,
        *,
        expression: str,
        labels: dict[str, str],
        start: str,
        end: str,
        max_values: int,
    ) -> list[dict[str, Any]]: ...


class ArgoCDBackend(Protocol):
    def get_application(self, *, name: str) -> dict[str, Any]: ...


class GitOpsBackend(Protocol):
    def read_file_at_commit(self, *, path: str, commit_sha: str, max_bytes: int) -> str: ...


@dataclass(frozen=True)
class ProviderBounds:
    max_conditions: int = MAX_CONDITIONS
    max_analysis_runs: int = MAX_ANALYSIS_RUNS
    max_pods: int = MAX_PODS
    max_events: int = MAX_EVENTS
    max_log_lines: int = MAX_LOG_LINES
    max_log_bytes: int = MAX_LOG_BYTES
    max_log_line_length: int = MAX_LOG_LINE_LENGTH
    max_prometheus_values: int = MAX_PROMETHEUS_VALUES
    max_gitops_file_bytes: int = MAX_GITOPS_FILE_BYTES


@dataclass
class KubernetesEvidenceAdapter:
    backend: KubernetesBackend
    bounds: ProviderBounds = field(default_factory=ProviderBounds)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_state(self, *, target: dict[str, Any]) -> dict[str, Any]:
        _require_target(target)
        self.calls.increment("kubernetes.get_frontend_state", {"target": target})
        try:
            rollout = _require_rollout_record(
                self.backend.get_rollout(namespace=APPROVED_NAMESPACE, name=APPROVED_WORKLOAD), self.bounds
            )
            return {
                "workload": _workload_from_rollout(rollout),
                "rollout": _rollout_from_record(rollout),
                "ingress": _require_mapping(
                    self.backend.get_ingress(namespace=APPROVED_NAMESPACE, name=APPROVED_INGRESS)
                ),
            }
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("kubernetes state backend failed") from exc

    def get_frontend_pod_status(
        self, *, target: dict[str, Any], selector: dict[str, str], max_pods: int
    ) -> list[dict[str, Any]]:
        _require_target(target)
        if selector != FRONTEND_SELECTOR:
            raise ProviderError("pod selector is not approved")
        _require_limit(max_pods, self.bounds.max_pods, "pod limit")
        self.calls.increment(
            "kubernetes.get_frontend_pod_status",
            {"target": target, "selector": selector, "max_pods": max_pods},
        )
        try:
            pods = _require_list(
                self.backend.list_pods(
                    namespace=APPROVED_NAMESPACE,
                    label_selector=FRONTEND_SELECTOR,
                    limit=max_pods,
                )
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("pod status backend failed") from exc
        if len(pods) > max_pods:
            raise ProviderError("pod status backend exceeded limit")
        return deepcopy(pods)

    def get_frontend_events(
        self, *, target: dict[str, Any], start: str, end: str, max_items: int
    ) -> list[dict[str, Any]]:
        _require_target(target)
        _require_time_range(start, end)
        _require_limit(max_items, self.bounds.max_events, "event limit")
        self.calls.increment(
            "kubernetes.get_frontend_events",
            {"target": target, "start": start, "end": end, "max_items": max_items},
        )
        events: list[dict[str, Any]] = []
        try:
            for involved_kind, involved_name in EVENT_OBJECTS:
                remaining = max_items - len(events)
                if remaining == 0:
                    break
                returned = _require_list(
                    self.backend.list_events(
                        namespace=APPROVED_NAMESPACE,
                        involved_kind=involved_kind,
                        involved_name=involved_name,
                        start=start,
                        end=end,
                        limit=remaining,
                    )
                )
                if len(returned) > remaining:
                    raise ProviderError("events backend exceeded limit")
                events.extend(returned)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("events backend failed") from exc
        return deepcopy(events)


@dataclass
class FrontendLogsAdapter:
    backend: FrontendLogBackend
    bounds: ProviderBounds = field(default_factory=ProviderBounds)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_container_logs(
        self,
        *,
        target: dict[str, Any],
        start: str,
        end: str,
        container: str,
        max_lines: int,
        max_bytes: int,
        max_line_length: int,
    ) -> list[dict[str, Any]]:
        _require_target(target)
        _require_time_range(start, end)
        if container != APPROVED_WORKLOAD:
            raise ProviderError("log container is not approved")
        _require_limit(max_lines, self.bounds.max_log_lines, "log line limit")
        _require_limit(max_bytes, self.bounds.max_log_bytes, "log byte limit")
        _require_limit(max_line_length, self.bounds.max_log_line_length, "log line length limit")
        self.calls.increment(
            "logs.get_frontend_container_logs",
            {
                "target": target,
                "start": start,
                "end": end,
                "container": container,
                "max_lines": max_lines,
                "max_bytes": max_bytes,
                "max_line_length": max_line_length,
            },
        )
        try:
            lines = _require_list(
                self.backend.read_frontend_container_logs(
                    namespace=APPROVED_NAMESPACE,
                    workload=APPROVED_WORKLOAD,
                    container=APPROVED_WORKLOAD,
                    start=start,
                    end=end,
                    max_lines=max_lines,
                    max_bytes=max_bytes,
                    max_line_length=max_line_length,
                )
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("logs backend failed") from exc
        if len(lines) > max_lines:
            raise ProviderError("logs backend exceeded line limit")
        message_bytes = 0
        for line in lines:
            record = _require_mapping(line)
            message = record.get("message")
            if not isinstance(message, str) or len(message) > max_line_length:
                raise ProviderError("logs backend exceeded line length limit")
            message_bytes += len(message.encode("utf-8"))
            if message_bytes > max_bytes:
                raise ProviderError("logs backend exceeded byte limit")
        return deepcopy(lines)


@dataclass
class PrometheusEvidenceAdapter:
    backend: PrometheusBackend
    bounds: ProviderBounds = field(default_factory=ProviderBounds)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def query_template(
        self, template_id: str, *, target: dict[str, Any], start: str, end: str, max_values: int
    ) -> list[dict[str, Any]]:
        _require_target(target)
        _require_time_range(start, end)
        expression = PROMETHEUS_RECORDING_RULES.get(template_id)
        if expression is None:
            raise ProviderError("prometheus template is not approved")
        _require_limit(max_values, self.bounds.max_prometheus_values, "prometheus value limit")
        labels: dict[str, str] = {}
        self.calls.increment(
            f"prometheus.query_template.{template_id}",
            {
                "template_id": template_id,
                "target": target,
                "start": start,
                "end": end,
                "max_values": max_values,
            },
        )
        try:
            values = _require_list(
                self.backend.query_recording_rule(
                    expression=expression,
                    labels=labels,
                    start=start,
                    end=end,
                    max_values=max_values,
                )
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("prometheus backend failed") from exc
        if len(values) > max_values:
            raise ProviderError("prometheus backend exceeded value limit")
        return deepcopy(values)


@dataclass
class ArgoCDGitOpsAdapter:
    argocd_backend: ArgoCDBackend
    gitops_backend: GitOpsBackend
    bounds: ProviderBounds = field(default_factory=ProviderBounds)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_deployment_revision(self, *, target: dict[str, Any]) -> dict[str, Any]:
        _require_target(target)
        self.calls.increment("gitops.get_deployment_revision", {"target": target})
        return self._resolve_deployment_revision()

    def _resolve_deployment_revision(self) -> dict[str, Any]:
        try:
            application = _require_mapping(self.argocd_backend.get_application(name=APPROVED_ARGOCD_APPLICATION))
            metadata = _require_mapping(application.get("metadata"))
            status = _require_mapping(application.get("status"))
            sync = _require_mapping(status.get("sync"))
            health = _require_mapping(status.get("health"))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("argocd backend failed") from exc
        if metadata.get("name") != APPROVED_ARGOCD_APPLICATION:
            raise ProviderError("argocd application is outside approved target")
        revision = sync.get("revision")
        if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
            raise ProviderError("argocd revision is not immutable")
        return {
            "application": APPROVED_ARGOCD_APPLICATION,
            "revision": revision,
            "sync_status": sync.get("status"),
            "health_status": health.get("status"),
        }

    def read_file_at_revision(
        self, path_id: str, revision: str, *, target: dict[str, Any], max_bytes: int
    ) -> dict[str, Any]:
        _require_target(target)
        path = GITOPS_PATHS.get(path_id)
        if path is None:
            raise ProviderError("gitops path is not approved")
        if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
            raise ProviderError("gitops revision is not immutable")
        _require_limit(max_bytes, self.bounds.max_gitops_file_bytes, "gitops file byte limit")
        resolved = self._resolve_deployment_revision()
        if revision != resolved["revision"]:
            raise ProviderError("gitops revision is not the resolved deployment revision")
        self.calls.increment(
            f"gitops.read_file_at_revision.{path_id}",
            {"path_id": path_id, "revision": revision, "target": target, "max_bytes": max_bytes},
        )
        try:
            content = self.gitops_backend.read_file_at_commit(path=path, commit_sha=revision, max_bytes=max_bytes)
        except Exception as exc:
            raise ProviderError("gitops backend failed") from exc
        if not isinstance(content, str) or len(content.encode("utf-8")) > max_bytes:
            raise ProviderError("gitops backend exceeded file limit")
        return {"path": path, "revision": revision, "content": content}


def _require_target(target: dict[str, Any]) -> None:
    if not isinstance(target, dict) or target != APPROVED_TARGET:
        raise ProviderError("target is not approved")


def _require_limit(value: Any, maximum: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ProviderError(f"{label} is not approved")


def _require_time_range(start: Any, end: Any) -> None:
    if not isinstance(start, str) or not isinstance(end, str):
        raise ProviderError("time range is malformed")
    try:
        start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProviderError("time range is malformed") from exc
    if start_time.tzinfo is None or end_time.tzinfo is None or end_time <= start_time:
        raise ProviderError("time range is malformed")


def _require_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError("backend object is malformed")
    return value


def _require_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ProviderError("backend collection is malformed")
    return value


def _require_rollout_record(value: Any, bounds: ProviderBounds) -> RolloutBackendRecord:
    raw = _require_mapping(value)
    required = {
        "name",
        "namespace",
        "ready_replicas",
        "desired_replicas",
        "available_replicas",
        "conditions",
        "phase",
        "current_step",
        "stable_service",
        "canary_service",
        "analysis_runs",
    }
    if not required.issubset(raw):
        raise ProviderError("rollout backend record is incomplete")
    conditions = _require_list(raw["conditions"])
    analysis_runs = _require_list(raw["analysis_runs"])
    if len(conditions) > bounds.max_conditions or len(analysis_runs) > bounds.max_analysis_runs:
        raise ProviderError("rollout backend record exceeds collection limit")
    return raw  # type: ignore[return-value]


def _workload_from_rollout(rollout: RolloutBackendRecord) -> dict[str, Any]:
    return {
        "name": rollout["name"],
        "namespace": rollout["namespace"],
        "ready_replicas": rollout["ready_replicas"],
        "desired_replicas": rollout["desired_replicas"],
        "available_replicas": rollout["available_replicas"],
        "conditions": deepcopy(rollout["conditions"]),
    }


def _rollout_from_record(rollout: RolloutBackendRecord) -> dict[str, Any]:
    return {
        "name": rollout["name"],
        "namespace": rollout["namespace"],
        "phase": rollout["phase"],
        "current_step": rollout["current_step"],
        "stable_service": rollout["stable_service"],
        "canary_service": rollout["canary_service"],
        "analysis_runs": deepcopy(rollout["analysis_runs"]),
    }
