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
    records: list[dict[str, Any]] = field(default_factory=list)

    def increment(self, name: str, params: dict[str, Any] | None = None) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1
        self.records.append({"name": name, "params": deepcopy(params or {})})

    def total(self) -> int:
        return sum(self.calls.values())

    def snapshot(self) -> dict[str, int]:
        return dict(sorted(self.calls.items()))

    def mark(self) -> int:
        return len(self.records)

    def snapshot_since(self, mark: int) -> dict[str, int]:
        calls: dict[str, int] = {}
        for record in self.records[mark:]:
            name = record["name"]
            calls[name] = calls.get(name, 0) + 1
        return dict(sorted(calls.items()))

    def records_since(self, mark: int) -> list[dict[str, Any]]:
        return deepcopy(self.records[mark:])


@dataclass
class FakeKubernetesProvider:
    workload_state: dict[str, Any] = field(default_factory=dict)
    rollout_state: dict[str, Any] = field(default_factory=dict)
    ingress_state: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_state(self, *, target: dict[str, Any]) -> dict[str, Any]:
        self.calls.increment("kubernetes.get_frontend_state", {"target": target})
        return {
            "workload": deepcopy(self.workload_state),
            "rollout": deepcopy(self.rollout_state),
            "ingress": deepcopy(self.ingress_state),
        }

    def get_frontend_events(self, *, target: dict[str, Any], start: str, end: str) -> list[dict[str, Any]]:
        self.calls.increment("kubernetes.get_frontend_events", {"target": target, "start": start, "end": end})
        return deepcopy(self.events)


@dataclass
class FakePrometheusProvider:
    values_by_template: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def query_template(self, template_id: str, *, target: dict[str, Any], start: str, end: str) -> list[dict[str, Any]]:
        self.calls.increment(
            f"prometheus.query_template.{template_id}",
            {"template_id": template_id, "target": target, "start": start, "end": end},
        )
        return deepcopy(self.values_by_template.get(template_id, []))


@dataclass
class FakeLogsProvider:
    lines: list[dict[str, Any]] = field(default_factory=list)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_frontend_container_logs(self, *, target: dict[str, Any], start: str, end: str, container: str) -> list[dict[str, Any]]:
        self.calls.increment(
            "logs.get_frontend_container_logs",
            {"target": target, "start": start, "end": end, "container": container},
        )
        return deepcopy(self.lines)


@dataclass
class FakeGitOpsProvider:
    application: dict[str, Any] = field(default_factory=dict)
    files_by_path_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: ProviderCalls = field(default_factory=ProviderCalls)

    def get_deployment_revision(self, *, target: dict[str, Any]) -> dict[str, Any]:
        self.calls.increment("gitops.get_deployment_revision", {"target": target})
        return deepcopy(self.application)

    def read_file_at_revision(self, path_id: str, revision: str, *, target: dict[str, Any]) -> dict[str, Any]:
        self.calls.increment(
            f"gitops.read_file_at_revision.{path_id}",
            {"path_id": path_id, "revision": revision, "target": target},
        )
        value = deepcopy(self.files_by_path_id.get(path_id, {}))
        value["revision"] = revision
        return value


@dataclass
class EvidenceProviders:
    kubernetes: FakeKubernetesProvider
    prometheus: FakePrometheusProvider
    logs: FakeLogsProvider
    gitops: FakeGitOpsProvider

    def mark(self) -> dict[str, int]:
        return {
            "kubernetes": self.kubernetes.calls.mark(),
            "prometheus": self.prometheus.calls.mark(),
            "logs": self.logs.calls.mark(),
            "gitops": self.gitops.calls.mark(),
        }

    def call_snapshot(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for provider in (self.kubernetes, self.prometheus, self.logs, self.gitops):
            merged.update(provider.calls.snapshot())
        return dict(sorted(merged.items()))

    def call_snapshot_since(self, mark: dict[str, int]) -> dict[str, int]:
        merged: dict[str, int] = {}
        for name, provider in (
            ("kubernetes", self.kubernetes),
            ("prometheus", self.prometheus),
            ("logs", self.logs),
            ("gitops", self.gitops),
        ):
            merged.update(provider.calls.snapshot_since(mark[name]))
        return dict(sorted(merged.items()))

    def call_records_since(self, mark: dict[str, int]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for name, provider in (
            ("kubernetes", self.kubernetes),
            ("prometheus", self.prometheus),
            ("logs", self.logs),
            ("gitops", self.gitops),
        ):
            for record in provider.calls.records_since(mark[name]):
                records.append({"provider": name, **record})
        return records

    def total_calls(self) -> int:
        return (
            self.kubernetes.calls.total()
            + self.prometheus.calls.total()
            + self.logs.calls.total()
            + self.gitops.calls.total()
        )
