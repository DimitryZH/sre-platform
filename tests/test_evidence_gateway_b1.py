from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime, timedelta

from evidence_gateway import (
    EvidenceGateway,
    EvidencePolicy,
    FakeGitOpsProvider,
    FakeKubernetesProvider,
    FakeLogsProvider,
    FakePrometheusProvider,
)
from evidence_gateway.policy import SCHEMA_VERSION, TargetPolicy
from evidence_gateway.policy import EvidenceLimits, ReplayGuard
from evidence_gateway.providers import EvidenceProviders, ProviderError


NOW = datetime(2026, 9, 19, 16, 0, tzinfo=UTC)
REVISION = "a" * 40


def iso(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def base_request(nonce: str = "nonce-001") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": "collect_staging_frontend_evidence",
        "request_id": f"req-{nonce}",
        "auth": {
            "subject": "staging-evidence-client",
            "audience": "sre-platform-evidence-gateway",
            "scope": "evidence.read.staging.frontend",
            "issued_at": iso(-1),
            "expires_at": iso(5),
            "nonce": nonce,
        },
        "time_range": {"start": iso(-20), "end": iso(0)},
        "evidence_kinds": [
            "kubernetes_state",
            "kubernetes_events",
            "logs",
            "prometheus",
            "deployment_revision",
            "gitops",
        ],
    }


def fake_providers() -> EvidenceProviders:
    return EvidenceProviders(
        kubernetes=FakeKubernetesProvider(
            workload_state={
                "name": "frontend",
                "namespace": "online-shop-stage",
                "ready_replicas": 1,
                "desired_replicas": 1,
                "available_replicas": 1,
                "conditions": [{"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"}],
                "raw_secret_like_extra": "must-not-appear",
            },
            rollout_state={
                "name": "frontend",
                "namespace": "online-shop-stage",
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
                "raw_status": {"ignored": True},
            },
            ingress_state={
                "name": "online-shop-frontend",
                "namespace": "online-shop-stage",
                "class_name": "nginx",
                "paths": ["/stage"],
                "annotations": {"ignored": "yes"},
            },
            events=[
                {
                    "timestamp": iso(-2),
                    "namespace": "online-shop-stage",
                    "type": "Normal",
                    "reason": "RolloutHealthy",
                    "message": "frontend rollout is healthy",
                    "involved_kind": "Rollout",
                    "involved_name": "frontend",
                    "metadata": {"ignored": True},
                }
            ],
        ),
        prometheus=FakePrometheusProvider(
            values_by_template={
                "slo_error_ratio_5m": [
                    {
                        "timestamp": iso(-1),
                        "value": 0.01,
                        "labels": {"exported_namespace": "online-shop-stage", "service": "frontend"},
                    }
                ],
                "slo_burn_rate_5m": [
                    {
                        "timestamp": iso(-1),
                        "value": 10.0,
                        "labels": {"exported_namespace": "online-shop-stage", "service": "frontend"},
                    }
                ],
            }
        ),
        logs=FakeLogsProvider(
            lines=[
                {
                    "timestamp": iso(-1),
                    "namespace": "online-shop-stage",
                    "workload": "frontend",
                    "pod": "frontend-abc",
                    "container": "frontend",
                    "message": "GET /stage returned 200",
                    "raw": {"ignored": True},
                }
            ]
        ),
        gitops=FakeGitOpsProvider(
            application={
                "application": "online-shop-stage",
                "revision": REVISION,
                "sync_status": "Synced",
                "health_status": "Healthy",
            },
            files_by_path_id={
                "stage_argocd_application": {
                    "path": "environments/stage/argocd/apps/online-shop-stage.yaml",
                    "content": "kind: Application\nmetadata:\n  name: online-shop-stage\n",
                },
                "stage_values": {
                    "path": "environments/stage/values/platform.yaml",
                    "content": "global:\n  namespace: online-shop-stage\n",
                },
                "frontend_rollout": {
                    "path": "charts/platform/templates/frontend-rollout.yaml",
                    "content": "kind: Rollout\nmetadata:\n  name: frontend\n",
                },
                "frontend_ingress": {
                    "path": "charts/platform/templates/frontend-ingress.yaml",
                    "content": "kind: Ingress\nmetadata:\n  name: online-shop-frontend\n",
                },
                "frontend_analysis_template": {
                    "path": "charts/platform/templates/frontend-slo-check-analysis-template.yaml",
                    "content": "kind: AnalysisTemplate\nmetadata:\n  name: frontend-slo-check\n",
                },
                "prometheus_rules": {
                    "path": "charts/platform/templates/prometheus-rules.yaml",
                    "content": "kind: PrometheusRule\nmetadata:\n  name: online-shop-slo-recording-rules\n",
                },
                "burn_rate_alerts": {
                    "path": "charts/platform/templates/burn-rate-alerts.yaml",
                    "content": "kind: PrometheusRule\nmetadata:\n  name: online-shop-burn-rate-alerts\n",
                },
            },
        ),
    )


def gateway(providers: EvidenceProviders | None = None, policy: EvidencePolicy | None = None) -> EvidenceGateway:
    return EvidenceGateway(policy or EvidencePolicy(), providers or fake_providers())


class EvidenceGatewayB1Tests(unittest.TestCase):
    def assert_denied_without_provider_calls(self, request: dict, code: str) -> None:
        providers = fake_providers()
        response = gateway(providers).collect(request, now=NOW)
        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], code)
        self.assertEqual(providers.total_calls(), 0)
        self.assertEqual(response["audit"]["provider_calls"], {})

    def test_positive_collects_sanitized_evidence_and_stable_ids(self) -> None:
        response = gateway().collect(base_request(), now=NOW)
        repeated = gateway().collect(base_request(), now=NOW)

        self.assertEqual(response["outcome"], "allowed")
        self.assertEqual(response["request_fingerprint"], repeated["request_fingerprint"])
        self.assertEqual(response["evidence_id"], repeated["evidence_id"])
        self.assertEqual(response["target"]["namespace"], "online-shop-stage")
        self.assertEqual(response["target"]["workload"], "frontend")
        serialized = str(response)
        self.assertNotIn("raw_secret_like_extra", serialized)
        self.assertNotIn("raw_status", serialized)
        self.assertNotIn("annotations", serialized)
        self.assertIn("kubernetes.get_frontend_state", response["audit"]["provider_calls"])
        self.assertIn("prometheus.query_template.slo_error_ratio_5m", response["audit"]["provider_calls"])
        self.assertEqual(response["audit"]["caller_identity"], "staging-evidence-client")
        self.assertEqual(response["audit"]["request_id"], "req-nonce-001")
        self.assertIn("result_digest", response["audit"])
        self.assertIn("response_bytes", response["audit"])

    def test_subset_kind_collects_only_requested_provider(self) -> None:
        request = base_request()
        request["auth"]["nonce"] = "nonce-subset"
        request["evidence_kinds"] = ["prometheus"]
        providers = fake_providers()

        response = gateway(providers).collect(request, now=NOW)

        self.assertEqual(response["outcome"], "allowed")
        self.assertEqual(set(response["audit"]["provider_calls"]), {
            "prometheus.query_template.slo_burn_rate_5m",
            "prometheus.query_template.slo_error_ratio_5m",
        })
        first_call = response["audit"]["provider_call_params"][0]
        self.assertEqual(first_call["params"]["start"], iso(-20))
        self.assertEqual(first_call["params"]["end"], iso(0))
        self.assertEqual(first_call["params"]["target"]["namespace"], "online-shop-stage")

    def test_unsafe_log_line_is_redacted_deterministically(self) -> None:
        providers = fake_providers()
        providers.logs.lines[0]["message"] = "token=super-secret-value"
        request = base_request("nonce-redaction")

        response = gateway(providers).collect(request, now=NOW)

        self.assertEqual(response["outcome"], "allowed")
        self.assertIn("[REDACTED-UNSAFE]", str(response))
        self.assertNotIn("super-secret-value", str(response))

    def test_request_fingerprint_ignores_auth_nonce(self) -> None:
        first = gateway().collect(base_request("nonce-a"), now=NOW)
        second = gateway().collect(base_request("nonce-b"), now=NOW)

        self.assertEqual(first["request_fingerprint"], second["request_fingerprint"])
        self.assertEqual(first["evidence_id"], second["evidence_id"])

    def test_write_mutation_intent_is_denied(self) -> None:
        request = base_request()
        request["patch_rollout"] = {"name": "frontend"}
        self.assert_denied_without_provider_calls(request, "mutation_intent")

    def test_wrong_namespace_is_denied(self) -> None:
        request = base_request()
        request["namespace"] = "default"
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_wrong_workload_is_denied(self) -> None:
        request = base_request()
        request["workload"] = "cart"
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_broad_kubernetes_read_is_denied(self) -> None:
        request = base_request()
        request["resource"] = "pods"
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_arbitrary_selector_is_denied(self) -> None:
        request = base_request()
        request["selector"] = "app.kubernetes.io/name in (frontend,cart)"
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_arbitrary_promql_is_denied(self) -> None:
        request = base_request()
        request["promql"] = '{__name__=~".*"}'
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_wildcard_promql_kind_is_denied(self) -> None:
        request = base_request()
        request["evidence_kinds"] = ["prometheus", "prometheus.write"]
        self.assert_denied_without_provider_calls(request, "mutation_intent")

    def test_unknown_template_id_is_denied_as_unsupported_kind(self) -> None:
        request = base_request()
        request["evidence_kinds"] = ["prometheus", "prometheus:up"]
        self.assert_denied_without_provider_calls(request, "unsupported_evidence_kind")

    def test_git_traversal_path_is_denied(self) -> None:
        request = base_request()
        request["path"] = "../terraform/main.tf"
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_git_wildcard_private_and_terraform_paths_are_denied(self) -> None:
        for field, value in [
            ("path", "*"),
            ("path_id", ".private/secret.yaml"),
            ("git_path", "terraform/main.tf"),
            ("ref", "main"),
        ]:
            request = base_request(f"nonce-{field}")
            request[field] = value
            self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_non_frontend_logs_request_is_denied(self) -> None:
        request = base_request()
        request["container"] = "cart"
        self.assert_denied_without_provider_calls(request, "malformed_request")

    def test_invalid_time_window_is_denied(self) -> None:
        request = base_request()
        request["time_range"] = {"start": iso(0), "end": iso(-1)}
        self.assert_denied_without_provider_calls(request, "invalid_time_range")

    def test_future_time_window_is_denied(self) -> None:
        request = base_request()
        request["time_range"] = {"start": iso(1), "end": iso(2)}
        self.assert_denied_without_provider_calls(request, "invalid_time_range")

    def test_over_sixty_minute_window_is_denied(self) -> None:
        request = base_request()
        request["time_range"] = {"start": iso(-61), "end": iso(0)}
        self.assert_denied_without_provider_calls(request, "invalid_time_range")

    def test_malformed_operation_is_denied(self) -> None:
        request = base_request()
        request["operation"] = "get"
        self.assert_denied_without_provider_calls(request, "unknown_operation")

    def test_unknown_operation_is_denied(self) -> None:
        request = base_request()
        request["operation"] = "collect_everything"
        self.assert_denied_without_provider_calls(request, "unknown_operation")

    def test_authentication_failure_is_denied(self) -> None:
        request = base_request()
        request["auth"]["subject"] = "unknown-subject"
        self.assert_denied_without_provider_calls(request, "unauthorized")

    def test_authorization_scope_failure_is_denied(self) -> None:
        request = base_request()
        request["auth"]["scope"] = "evidence.read.all"
        self.assert_denied_without_provider_calls(request, "unauthorized")

    def test_stale_auth_is_denied(self) -> None:
        request = base_request()
        request["auth"]["issued_at"] = iso(-30)
        request["auth"]["expires_at"] = iso(-20)
        self.assert_denied_without_provider_calls(request, "stale_request")

    def test_replay_nonce_is_denied_without_provider_calls(self) -> None:
        providers = fake_providers()
        policy = EvidencePolicy()
        first = gateway(providers, policy).collect(base_request("nonce-replay"), now=NOW)
        self.assertEqual(first["outcome"], "allowed")
        before = providers.total_calls()

        second = gateway(providers, policy).collect(base_request("nonce-replay"), now=NOW)

        self.assertEqual(second["outcome"], "denied")
        self.assertEqual(second["error"]["code"], "replay_rejected")
        self.assertEqual(providers.total_calls(), before)
        self.assertEqual(second["audit"]["provider_calls"], {})
        self.assertEqual(second["audit"]["provider_call_count"], 0)

    def test_malformed_request_does_not_consume_nonce(self) -> None:
        policy = EvidencePolicy()
        bad = base_request("nonce-after-malformed")
        bad["time_range"] = {"start": iso(0), "end": iso(-1)}
        bad_response = gateway(fake_providers(), policy).collect(bad, now=NOW)

        good_response = gateway(fake_providers(), policy).collect(base_request("nonce-after-malformed"), now=NOW)

        self.assertEqual(bad_response["outcome"], "denied")
        self.assertEqual(bad_response["error"]["code"], "invalid_time_range")
        self.assertEqual(good_response["outcome"], "allowed")

    def test_ambiguous_server_target_policy_is_denied_without_provider_calls(self) -> None:
        policy = EvidencePolicy(target=TargetPolicy(log_containers=("frontend", "cart")))
        providers = fake_providers()

        response = gateway(providers, policy).collect(base_request(), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "ambiguous_target")
        self.assertEqual(providers.total_calls(), 0)

    def test_provider_out_of_scope_workload_fails_closed(self) -> None:
        providers = fake_providers()
        providers.kubernetes.workload_state["name"] = "cart"
        response = gateway(providers).collect(base_request("nonce-provider-scope"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_provider_broad_event_fails_closed(self) -> None:
        providers = fake_providers()
        providers.kubernetes.events[0]["involved_name"] = "cart"
        response = gateway(providers).collect(base_request("nonce-broad-event"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_secret_frontend_event_fails_closed(self) -> None:
        providers = fake_providers()
        providers.kubernetes.events[0]["involved_kind"] = "Secret"
        providers.kubernetes.events[0]["involved_name"] = "frontend"
        response = gateway(providers).collect(base_request("nonce-secret-event"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_event_timestamp_outside_window_fails_closed(self) -> None:
        providers = fake_providers()
        providers.kubernetes.events[0]["timestamp"] = iso(-21)
        response = gateway(providers).collect(base_request("nonce-old-event"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "outside_time_range")

    def test_unowned_analysis_run_fails_closed(self) -> None:
        providers = fake_providers()
        providers.kubernetes.rollout_state["analysis_runs"][0]["owner_name"] = "cart"
        response = gateway(providers).collect(base_request("nonce-unowned-analysis"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_stage_private_ingress_path_fails_closed(self) -> None:
        providers = fake_providers()
        providers.kubernetes.ingress_state["paths"] = ["/stage-private"]
        response = gateway(providers).collect(base_request("nonce-stage-private"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_provider_non_frontend_log_fails_closed(self) -> None:
        providers = fake_providers()
        providers.logs.lines[0]["container"] = "cart"
        response = gateway(providers).collect(base_request("nonce-cart-log"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_log_timestamp_outside_window_fails_closed(self) -> None:
        providers = fake_providers()
        providers.logs.lines[0]["timestamp"] = iso(-21)
        response = gateway(providers).collect(base_request("nonce-old-log"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "outside_time_range")

    def test_oversized_logs_fail_closed(self) -> None:
        providers = fake_providers()
        providers.logs.lines = [copy.deepcopy(providers.logs.lines[0]) for _ in range(81)]
        response = gateway(providers).collect(base_request("nonce-big-logs"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "oversized_result")

    def test_oversized_nested_conditions_fail_closed_before_projection(self) -> None:
        providers = fake_providers()
        providers.kubernetes.workload_state["conditions"] = [
            {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"}
            for _ in range(9)
        ]
        response = gateway(providers).collect(base_request("nonce-big-conditions"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "oversized_result")

    def test_oversized_gitops_file_fails_closed(self) -> None:
        providers = fake_providers()
        providers.gitops.files_by_path_id["stage_values"]["content"] = "a" * 5000
        response = gateway(providers).collect(base_request("nonce-big-gitops"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "oversized_result")

    def test_gitops_path_mismatch_fails_closed(self) -> None:
        providers = fake_providers()
        providers.gitops.files_by_path_id["stage_values"]["path"] = "terraform/main.tf"
        response = gateway(providers).collect(base_request("nonce-path-mismatch"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_gitops_revision_must_be_immutable_commit_sha(self) -> None:
        providers = fake_providers()
        providers.gitops.application["revision"] = "main"
        response = gateway(providers).collect(base_request("nonce-bad-revision"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "malformed_provider_data")

    def test_prometheus_out_of_scope_namespace_fails_closed(self) -> None:
        providers = fake_providers()
        providers.prometheus.values_by_template["slo_error_ratio_5m"][0]["labels"]["exported_namespace"] = "default"
        response = gateway(providers).collect(base_request("nonce-prom-scope"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_prometheus_conflicting_service_fails_closed(self) -> None:
        providers = fake_providers()
        providers.prometheus.values_by_template["slo_error_ratio_5m"][0]["labels"]["service"] = "cart"
        response = gateway(providers).collect(base_request("nonce-prom-cart"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_prometheus_conflicting_ingress_fails_closed(self) -> None:
        providers = fake_providers()
        providers.prometheus.values_by_template["slo_error_ratio_5m"][0]["labels"]["ingress"] = "stage-private"
        response = gateway(providers).collect(base_request("nonce-prom-ingress"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "out_of_scope")

    def test_prometheus_timestamp_outside_window_fails_closed(self) -> None:
        providers = fake_providers()
        providers.prometheus.values_by_template["slo_error_ratio_5m"][0]["timestamp"] = iso(-21)
        response = gateway(providers).collect(base_request("nonce-old-prom"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "outside_time_range")

    def test_prometheus_non_finite_value_fails_closed(self) -> None:
        for value, nonce in [(float("nan"), "nonce-prom-nan"), (float("inf"), "nonce-prom-inf")]:
            providers = fake_providers()
            providers.prometheus.values_by_template["slo_error_ratio_5m"][0]["value"] = value
            response = gateway(providers).collect(base_request(nonce), now=NOW)

            self.assertEqual(response["outcome"], "denied")
            self.assertEqual(response["error"]["code"], "malformed_provider_data")

    def test_non_string_projected_field_fails_closed(self) -> None:
        providers = fake_providers()
        providers.logs.lines[0]["message"] = {"raw": "not allowed"}
        response = gateway(providers).collect(base_request("nonce-non-string"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "malformed_provider_data")

    def test_provider_unavailable_is_safe_backend_unavailable(self) -> None:
        class UnavailablePrometheusProvider(FakePrometheusProvider):
            def query_template(self, template_id, *, target, start, end):
                self.calls.increment(
                    f"prometheus.query_template.{template_id}",
                    {"template_id": template_id, "target": target, "start": start, "end": end},
                )
                raise ProviderError("secret backend detail")

        providers = fake_providers()
        providers.prometheus = UnavailablePrometheusProvider(providers.prometheus.values_by_template)
        response = gateway(providers).collect(base_request("nonce-provider-error"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "backend_unavailable")
        self.assertNotIn("secret backend detail", str(response))

    def test_malformed_provider_output_is_safe_error(self) -> None:
        providers = fake_providers()
        providers.kubernetes.workload_state = []
        response = gateway(providers).collect(base_request("nonce-malformed-output"), now=NOW)

        self.assertEqual(response["outcome"], "denied")
        self.assertEqual(response["error"]["code"], "malformed_provider_data")

    def test_cumulative_audit_does_not_contaminate_current_request(self) -> None:
        providers = fake_providers()
        policy = EvidencePolicy()
        first_request = base_request("nonce-audit-first")
        first_request["evidence_kinds"] = ["logs"]
        first = gateway(providers, policy).collect(first_request, now=NOW)
        self.assertEqual(first["outcome"], "allowed")

        second_request = base_request("nonce-audit-second")
        second_request["evidence_kinds"] = ["prometheus"]
        second = gateway(providers, policy).collect(second_request, now=NOW)

        self.assertEqual(second["outcome"], "allowed")
        self.assertEqual(set(second["audit"]["provider_calls"]), {
            "prometheus.query_template.slo_burn_rate_5m",
            "prometheus.query_template.slo_error_ratio_5m",
        })
        self.assertNotIn("logs.get_frontend_container_logs", second["audit"]["provider_calls"])

    def test_replay_state_expires_nonce(self) -> None:
        providers = fake_providers()
        policy = EvidencePolicy()
        first_response = gateway(providers, policy).collect(base_request("nonce-expiring"), now=NOW)
        self.assertEqual(first_response["outcome"], "allowed")

        second = base_request("nonce-expiring")
        second["auth"]["issued_at"] = iso(5)
        second["auth"]["expires_at"] = iso(10)
        second_response = gateway(providers, policy).collect(second, now=NOW + timedelta(minutes=6))

        self.assertEqual(second_response["outcome"], "allowed")

    def test_replay_state_capacity_fails_closed(self) -> None:
        policy = EvidencePolicy(limits=EvidenceLimits(max_replay_entries=1), replay_guard=ReplayGuard())
        first = gateway(fake_providers(), policy).collect(base_request("nonce-capacity-a"), now=NOW)
        second = gateway(fake_providers(), policy).collect(base_request("nonce-capacity-b"), now=NOW)

        self.assertEqual(first["outcome"], "allowed")
        self.assertEqual(second["outcome"], "denied")
        self.assertEqual(second["error"]["code"], "replay_capacity_exceeded")
        self.assertEqual(second["audit"]["provider_calls"], {})


if __name__ == "__main__":
    unittest.main()
