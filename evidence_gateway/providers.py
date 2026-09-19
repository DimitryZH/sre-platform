"""Fake provider contracts for the offline Evidence Gateway B1 core."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    pass


@dataclass
class ProviderCalls:
    calls: dict[str, int] = field(default_factory=dict)

    def increment(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def total(self) -> int:
        return sum(self.calls.values())

    def snapshot(self) -> dict[str, int]:
        return dict(sorted(self.calls.items()))


@dataclass
class FakeKubernetesProvider:
    workload_state: dict[str, Any] = field(default_factory=dict)
    rollout_state: dict[str, Any] = field(default_factory=dict)
    ingress_state: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_state(self) -> dict[str, Any]:
        self.calls.increment("kubernetes.get_frontend_state")
        return {
            "workload": deepcopy(self.workload_state),
            "rollout": deepcopy(self.rollout_state),
            "ingress": deepcopy(self.ingress_state),
        }

    def get_frontend_events(self) -> list[dict[str, Any]]:
        self.calls.increment("kubernetes.get_frontend_events")
        return deepcopy(self.events)


@dataclass
class FakePrometheusProvider:
    values_by_template: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def query_template(self, template_id: str) -> list[dict[str, Any]]:
        self.calls.increment(f"prometheus.query_template.{template_id}")
        return deepcopy(self.values_by_template.get(template_id, []))


@dataclass
class FakeLogsProvider:
    lines: list[dict[str, Any]] = field(default_factory=list)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_container_logs(self) -> list[dict[str, Any]]:
        self.calls.increment("logs.get_frontend_container_logs")
        return deepcopy(self.lines)


@dataclass
class FakeGitOpsProvider:
    application: dict[str, Any] = field(default_factory=dict)
    files_by_path_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_deployment_revision(self) -> dict[str, Any]:
        self.calls.increment("gitops.get_deployment_revision")
        return deepcopy(self.application)

    def read_file_at_revision(self, path_id: str, revision: str) -> dict[str, Any]:
        self.calls.increment(f"gitops.read_file_at_revision.{path_id}")
        value = deepcopy(self.files_by_path_id.get(path_id, {}))
        value["revision"] = revision
        return value


@dataclass
class EvidenceProviders:
    kubernetes: FakeKubernetesProvider
    prometheus: FakePrometheusProvider
    logs: FakeLogsProvider
    gitops: FakeGitOpsProvider

    def call_snapshot(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for provider in (self.kubernetes, self.prometheus, self.logs, self.gitops):
            merged.update(provider.calls.snapshot())
        return dict(sorted(merged.items()))

    def total_calls(self) -> int:
        return (
            self.kubernetes.calls.total()
            + self.prometheus.calls.total()
            + self.logs.calls.total()
            + self.gitops.calls.total()
        )
