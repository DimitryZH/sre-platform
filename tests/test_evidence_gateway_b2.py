from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from evidence_gateway import (
    ArgoCDGitOpsAdapter,
    EvidenceGateway,
    EvidencePolicy,
    FakeGitOpsProvider,
    FakeKubernetesProvider,
    FakeLogsProvider,
    KubernetesEvidenceAdapter,
    PrometheusEvidenceAdapter,
)
from evidence_gateway.adapters import FRONTEND_SELECTOR, GITOPS_PATHS, PROMETHEUS_RECORDING_RULES, FrontendLogsAdapter
from evidence_gateway.policy import SCHEMA_VERSION, TargetPolicy
from evidence_gateway.providers import EvidenceProviders, ProviderError


NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
START = "2026-09-20T15:40:00Z"
END = "2026-09-20T16:00:00Z"
REVISION = "b" * 40
TARGET = TargetPolicy().to_public_dict()


def all_evidence_request(nonce: str = "nonce-b2-all") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "collect_staging_frontend_evidence",
        "request_id": f"req-{nonce}",
        "auth": {
            "subject": "staging-evidence-client",
            "audience": "sre-platform-evidence-gateway",
            "scope": "evidence.read.staging.frontend",
            "issued_at": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "nonce": nonce,
        },
        "time_range": {"start": START, "end": END},
        "evidence_kinds": [
            "kubernetes_state",
            "kubernetes_pod_status",
            "kubernetes_events",
            "logs",
            "prometheus",
            "deployment_revision",
            "gitops",
        ],
    }


class RecordingKubernetesBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.pods: list[dict[str, Any]] = []
        self.events: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.rollout: dict[str, Any] = {
            "name": "frontend",
            "namespace": "online-shop-stage",
            "ready_replicas": 1,
            "desired_replicas": 1,
            "available_replicas": 1,
            "conditions": [{"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"}],
            "phase": "Healthy",
            "current_step": 7,
            "stable_service": "frontend",
            "canary_service": "frontend-canary",
            "analysis_runs": [
                {
                    "name": "frontend-slo-check-abc",
                    "phase": "Successful",
                    "owner_kind": "Rollout",
                    "owner_name": "frontend",
                }
            ],
        }

    def get_rollout(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"operation": "get_rollout", **kwargs})
        return self.rollout

    def get_ingress(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"operation": "get_ingress", **kwargs})
        return {
            "name": "online-shop-frontend",
            "namespace": "online-shop-stage",
            "class_name": "nginx",
            "paths": ["/stage"],
        }

    def list_pods(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"operation": "list_pods", **kwargs})
        return self.pods

    def list_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"operation": "list_events", **kwargs})
        return self.events.get((kwargs["involved_kind"], kwargs["involved_name"]), [])


class RecordingLogsBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []

    def read_frontend_container_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self.lines


class RecordingPrometheusBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.values: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def query_recording_rule(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.values


class RecordingArgoCDBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.application: dict[str, Any] = {
            "metadata": {"name": "online-shop-stage"},
            "status": {
                "sync": {"revision": REVISION, "status": "Synced"},
                "health": {"status": "Healthy"},
            },
        }

    def get_application(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.application


class RecordingGitOpsBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = "kind: ConfigMap\n"

    def read_file_at_commit(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.content


class EvidenceGatewayB2Tests(unittest.TestCase):
    def test_kubernetes_adapter_uses_only_exact_object_and_selector_requests(self) -> None:
        backend = RecordingKubernetesBackend()
        adapter = KubernetesEvidenceAdapter(backend)

        adapter.get_frontend_state(target=TARGET)
        adapter.get_frontend_pod_status(target=TARGET, selector=FRONTEND_SELECTOR, max_pods=10)
        adapter.get_frontend_events(target=TARGET, start=START, end=END, max_items=16)

        self.assertEqual(
            backend.calls[:2],
            [
                {"operation": "get_rollout", "namespace": "online-shop-stage", "name": "frontend"},
                {"operation": "get_ingress", "namespace": "online-shop-stage", "name": "online-shop-frontend"},
            ],
        )
        self.assertNotIn("get_deployment", [call["operation"] for call in backend.calls])
        self.assertEqual(
            backend.calls[2],
            {
                "operation": "list_pods",
                "namespace": "online-shop-stage",
                "label_selector": {"app.kubernetes.io/name": "frontend"},
                "limit": 10,
            },
        )
        self.assertEqual(
            backend.calls[3:],
            [
                {
                    "operation": "list_events",
                    "namespace": "online-shop-stage",
                    "involved_kind": kind,
                    "involved_name": name,
                    "start": START,
                    "end": END,
                    "limit": 16,
                }
                for kind, name in (("Rollout", "frontend"), ("Ingress", "online-shop-frontend"))
            ],
        )

    def test_event_requests_consume_a_single_bounded_total(self) -> None:
        backend = RecordingKubernetesBackend()
        backend.events[("Rollout", "frontend")] = [{"index": item} for item in range(15)]
        backend.events[("Ingress", "online-shop-frontend")] = [{"index": 1}]
        adapter = KubernetesEvidenceAdapter(backend)

        events = adapter.get_frontend_events(target=TARGET, start=START, end=END, max_items=16)

        self.assertEqual(len(events), 16)
        self.assertEqual(backend.calls[0]["limit"], 16)
        self.assertEqual(backend.calls[1]["limit"], 1)
        self.assertEqual(len(backend.calls), 2)

    def test_logs_and_prometheus_use_server_owned_templates_and_limits(self) -> None:
        logs_backend = RecordingLogsBackend()
        logs = FrontendLogsAdapter(logs_backend)
        prom_backend = RecordingPrometheusBackend()
        prometheus = PrometheusEvidenceAdapter(prom_backend)

        logs.get_frontend_container_logs(
            target=TARGET,
            start=START,
            end=END,
            container="frontend",
            max_lines=80,
            max_bytes=8192,
            max_line_length=240,
        )
        prometheus.query_template("slo_error_ratio_5m", target=TARGET, start=START, end=END, max_values=16)

        self.assertEqual(
            logs_backend.calls,
            [
                {
                    "namespace": "online-shop-stage",
                    "workload": "frontend",
                    "container": "frontend",
                    "start": START,
                    "end": END,
                    "max_lines": 80,
                    "max_bytes": 8192,
                    "max_line_length": 240,
                }
            ],
        )
        self.assertEqual(
            prom_backend.calls,
            [
                {
                    "expression": PROMETHEUS_RECORDING_RULES["slo_error_ratio_5m"],
                    "labels": {},
                    "start": START,
                    "end": END,
                    "max_values": 16,
                }
            ],
        )

    def test_gitops_adapter_binds_allowlisted_path_to_resolved_immutable_revision(self) -> None:
        argocd = RecordingArgoCDBackend()
        gitops = RecordingGitOpsBackend()
        adapter = ArgoCDGitOpsAdapter(argocd, gitops)

        revision = adapter.get_deployment_revision(target=TARGET)
        file_result = adapter.read_file_at_revision(
            "stage_values", revision["revision"], target=TARGET, max_bytes=4096
        )

        self.assertEqual(argocd.calls, [{"name": "online-shop-stage"}, {"name": "online-shop-stage"}])
        self.assertEqual(
            gitops.calls,
            [{"path": GITOPS_PATHS["stage_values"], "commit_sha": REVISION, "max_bytes": 4096}],
        )
        self.assertEqual(file_result["revision"], REVISION)
        with self.assertRaises(ProviderError):
            adapter.read_file_at_revision("stage_values", "c" * 40, target=TARGET, max_bytes=4096)
        self.assertEqual(len(gitops.calls), 1)

    def test_gitops_adapter_fails_closed_when_application_revision_drifts(self) -> None:
        argocd = RecordingArgoCDBackend()
        gitops = RecordingGitOpsBackend()
        adapter = ArgoCDGitOpsAdapter(argocd, gitops)
        revision = adapter.get_deployment_revision(target=TARGET)["revision"]
        argocd.application["status"]["sync"]["revision"] = "c" * 40

        with self.assertRaises(ProviderError):
            adapter.read_file_at_revision("stage_values", revision, target=TARGET, max_bytes=4096)

        self.assertEqual(gitops.calls, [])
        self.assertEqual(argocd.calls, [{"name": "online-shop-stage"}, {"name": "online-shop-stage"}])

    def test_limits_and_broad_or_ambiguous_access_fail_before_backend_calls(self) -> None:
        kubernetes_backend = RecordingKubernetesBackend()
        kubernetes = KubernetesEvidenceAdapter(kubernetes_backend)
        logs_backend = RecordingLogsBackend()
        logs = FrontendLogsAdapter(logs_backend)
        prometheus_backend = RecordingPrometheusBackend()
        prometheus = PrometheusEvidenceAdapter(prometheus_backend)
        argocd = RecordingArgoCDBackend()
        gitops_backend = RecordingGitOpsBackend()
        gitops = ArgoCDGitOpsAdapter(argocd, gitops_backend)
        bad_target = {**TARGET, "namespace": "default"}

        with self.assertRaises(ProviderError):
            kubernetes.get_frontend_pod_status(target=bad_target, selector=FRONTEND_SELECTOR, max_pods=10)
        with self.assertRaises(ProviderError):
            kubernetes.get_frontend_pod_status(
                target=TARGET, selector={"app.kubernetes.io/name": "frontend,cart"}, max_pods=10
            )
        with self.assertRaises(ProviderError):
            kubernetes.get_frontend_events(target=TARGET, start=START, end=END, max_items=17)
        with self.assertRaises(ProviderError):
            logs.get_frontend_container_logs(
                target=TARGET,
                start=START,
                end=END,
                container="cart",
                max_lines=80,
                max_bytes=8192,
                max_line_length=240,
            )
        with self.assertRaises(ProviderError):
            prometheus.query_template("up", target=TARGET, start=START, end=END, max_values=16)
        with self.assertRaises(ProviderError):
            prometheus.query_template("slo_error_ratio_5m", target=TARGET, start=START, end=END, max_values=17)
        with self.assertRaises(ProviderError):
            gitops.read_file_at_revision("../secrets", REVISION, target=TARGET, max_bytes=4096)
        with self.assertRaises(ProviderError):
            gitops.read_file_at_revision("stage_values", "main", target=TARGET, max_bytes=4096)
        with self.assertRaises(ProviderError):
            gitops.read_file_at_revision("stage_values", REVISION, target=TARGET, max_bytes=4097)

        self.assertEqual(kubernetes_backend.calls, [])
        self.assertEqual(logs_backend.calls, [])
        self.assertEqual(prometheus_backend.calls, [])
        self.assertEqual(argocd.calls, [])
        self.assertEqual(gitops_backend.calls, [])

    def test_backend_limit_violation_and_error_map_to_provider_error(self) -> None:
        kubernetes_backend = RecordingKubernetesBackend()
        kubernetes_backend.pods = [{"pod": item} for item in range(11)]
        kubernetes = KubernetesEvidenceAdapter(kubernetes_backend)
        prometheus_backend = RecordingPrometheusBackend()
        prometheus_backend.error = RuntimeError("token=backend-secret")
        prometheus = PrometheusEvidenceAdapter(prometheus_backend)

        with self.assertRaises(ProviderError):
            kubernetes.get_frontend_pod_status(target=TARGET, selector=FRONTEND_SELECTOR, max_pods=10)
        with self.assertRaises(ProviderError) as error:
            prometheus.query_template("slo_error_ratio_5m", target=TARGET, start=START, end=END, max_values=16)

        self.assertNotIn("backend-secret", str(error.exception))

    def test_gateway_maps_adapter_backend_failure_to_safe_response(self) -> None:
        prom_backend = RecordingPrometheusBackend()
        prom_backend.error = RuntimeError("password=do-not-leak")
        providers = EvidenceProviders(
            kubernetes=FakeKubernetesProvider(),
            prometheus=PrometheusEvidenceAdapter(prom_backend),
            logs=FakeLogsProvider(),
            gitops=FakeGitOpsProvider(),
        )
        request = {
            "schema_version": SCHEMA_VERSION,
            "operation": "collect_staging_frontend_evidence",
            "request_id": "req-b2-provider-error",
            "auth": {
                "subject": "staging-evidence-client",
                "audience": "sre-platform-evidence-gateway",
                "scope": "evidence.read.staging.frontend",
                "issued_at": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "expires_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "nonce": "nonce-b2-provider-error",
            },
            "time_range": {"start": START, "end": END},
            "evidence_kinds": ["prometheus"],
        }

        response = EvidenceGateway(EvidencePolicy(), providers).collect(request, now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "backend_unavailable")
        self.assertNotIn("do-not-leak", str(response))
        self.assertEqual(response["audit"]["provider_calls"], {"prometheus.query_template.slo_burn_rate_5m": 1})

    def test_gateway_collects_complete_evidence_from_all_offline_adapters(self) -> None:
        kubernetes_backend = RecordingKubernetesBackend()
        kubernetes_backend.pods = [
            {
                "pod_id": "frontend-abc",
                "namespace": "online-shop-stage",
                "workload": "frontend",
                "container": "frontend",
                "phase": "Running",
                "ready": True,
                "restart_count": 1,
                "container_state": "Running",
                "node_name": "must-not-appear",
            }
        ]
        kubernetes_backend.events[("Rollout", "frontend")] = [
            {
                "timestamp": "2026-09-20T15:50:00Z",
                "namespace": "online-shop-stage",
                "type": "Normal",
                "reason": "RolloutHealthy",
                "message": "frontend rollout is healthy",
                "involved_kind": "Rollout",
                "involved_name": "frontend",
            }
        ]
        logs_backend = RecordingLogsBackend()
        logs_backend.lines = [
            {
                "timestamp": "2026-09-20T15:50:00Z",
                "namespace": "online-shop-stage",
                "workload": "frontend",
                "pod": "frontend-abc",
                "container": "frontend",
                "message": "GET /stage returned 200",
            }
        ]
        prometheus_backend = RecordingPrometheusBackend()
        prometheus_backend.values = [{"timestamp": "2026-09-20T15:50:00Z", "value": 0.01, "labels": {}}]
        argocd_backend = RecordingArgoCDBackend()
        gitops_backend = RecordingGitOpsBackend()
        providers = EvidenceProviders(
            kubernetes=KubernetesEvidenceAdapter(kubernetes_backend),
            prometheus=PrometheusEvidenceAdapter(prometheus_backend),
            logs=FrontendLogsAdapter(logs_backend),
            gitops=ArgoCDGitOpsAdapter(argocd_backend, gitops_backend),
        )

        response = EvidenceGateway(EvidencePolicy(), providers).collect(all_evidence_request(), now=NOW)

        self.assertEqual(response["outcome"], "allowed")
        self.assertRegex(response["request_fingerprint"], r"^[a-f0-9]{64}$")
        self.assertRegex(response["evidence_id"], r"^[a-f0-9]{64}$")
        self.assertEqual(response["result"]["source_revision"], REVISION)
        self.assertEqual(response["audit"]["revision"], REVISION)
        self.assertEqual(response["result"]["section_count"], 7)
        self.assertNotIn("get_deployment", [call["operation"] for call in kubernetes_backend.calls])
        self.assertNotIn("must-not-appear", str(response))
        self.assertEqual(prometheus_backend.calls[0]["labels"], {})
        self.assertIn("kubernetes.get_frontend_state", response["audit"]["provider_calls"])
        self.assertIn("gitops.read_file_at_revision.stage_values", response["audit"]["provider_calls"])


if __name__ == "__main__":
    unittest.main()
