from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from evidence_gateway.policy import SCHEMA_VERSION, TargetPolicy
from evidence_gateway.providers import EvidenceProviders, ProviderError
from evidence_gateway.runtime.gateway import (
    EXTERNAL_TOKEN_AUDIENCE,
    HTTP_OPERATION_PATH,
    VALIDATION_KUBERNETES_SUBJECT,
    RuntimeGateway,
    RuntimeTokenConfig,
    PrivateHTTPApplication,
    TokenReviewResult,
)
from evidence_gateway.runtime.state import RuntimeState, RuntimeStateLimits, RuntimeStateError
from evidence_gateway.runtime.transports import (
    GATEWAY_URI_SAN,
    GITHUB_PROXY_DNS_SAN,
    GITHUB_PROXY_URI_SAN,
    SOURCE_DNS_SAN,
    SOURCE_URI_SAN,
    GitHubProxyResponse,
    MTLSGitHubProxyClient,
    MTLSSourceClient,
    PeerCertificate,
    RuntimeGitOpsProvider,
    RuntimeKubernetesProvider,
    RuntimeUnavailableLogsProvider,
    RuntimeUnavailablePrometheusProvider,
)


NOW = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)
REVISION = "c" * 40
TARGET = TargetPolicy().to_public_dict()


class FakeTokenReviewer:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.result = TokenReviewResult(
            authenticated=True,
            username=VALIDATION_KUBERNETES_SUBJECT,
            audiences=frozenset({EXTERNAL_TOKEN_AUDIENCE}),
        )

    def review(self, *, token: str, audience: str) -> TokenReviewResult:
        self.calls.append({"token": token, "audience": audience})
        return self.result


class FakeSource:
    def __init__(self) -> None:
        self.state_calls = 0
        self.revision_calls = 0
        self.state = {
            "workload": {
                "name": "frontend",
                "namespace": "online-shop-stage",
                "ready_replicas": 1,
                "desired_replicas": 1,
                "available_replicas": 1,
                "conditions": [{"type": "Available", "status": "True", "reason": "Healthy"}],
            },
            "rollout": {
                "name": "frontend",
                "namespace": "online-shop-stage",
                "phase": "Healthy",
                "current_step": 1,
                "stable_service": "frontend",
                "canary_service": "frontend-canary",
                "analysis_runs": [],
            },
            "ingress": {
                "name": "online-shop-frontend",
                "namespace": "online-shop-stage",
                "class_name": "nginx",
                "paths": ["/stage"],
            },
        }
        self.application = {
            "application": "online-shop-stage",
            "revision": REVISION,
            "sync_status": "Synced",
            "health_status": "Healthy",
        }

    def get_frontend_state(self) -> dict[str, Any]:
        self.state_calls += 1
        return self.state

    def get_deployment_revision(self) -> dict[str, Any]:
        self.revision_calls += 1
        return self.application


class FakeGitHubProxy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = 200
        self.content = "kind: ConfigMap\n"
        self.raw_body: bytes | None = None

    def read_allowlisted_contents(self, *, path_id: str, revision: str, max_response_bytes: int) -> GitHubProxyResponse:
        self.calls.append({"path_id": path_id, "revision": revision, "max_response_bytes": max_response_bytes})
        path = {
            "stage_argocd_application": "environments/stage/argocd/apps/online-shop-stage.yaml",
            "stage_values": "environments/stage/values/platform.yaml",
            "frontend_rollout": "charts/platform/templates/frontend-rollout.yaml",
            "frontend_ingress": "charts/platform/templates/frontend-ingress.yaml",
            "frontend_analysis_template": "charts/platform/templates/frontend-slo-check-analysis-template.yaml",
            "prometheus_rules": "charts/platform/templates/prometheus-rules.yaml",
            "burn_rate_alerts": "charts/platform/templates/burn-rate-alerts.yaml",
        }[path_id]
        body = self.raw_body or json.dumps(
            {"path": path, "encoding": "base64", "content": base64.b64encode(self.content.encode()).decode()}
        ).encode()
        return GitHubProxyResponse(status=self.status, body=body)


def certificate(*, dns: str | None = None, uri: str, trusted: bool = True) -> PeerCertificate:
    return PeerCertificate(
        trusted_by_configured_ca=trusted,
        dns_names=frozenset(() if dns is None else {dns}),
        uri_sans=frozenset({uri}),
        not_after=NOW + timedelta(days=1),
    )


class RuntimeGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = RuntimeState(Path(self.temp.name) / "runtime.db")
        self.reviewer = FakeTokenReviewer()
        self.source = FakeSource()
        self.github_proxy = FakeGitHubProxy()
        source_client = MTLSSourceClient(
            self.source,
            certificate(uri=GATEWAY_URI_SAN),
            certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN),
        )
        github_client = MTLSGitHubProxyClient(
            self.github_proxy,
            certificate(uri=GATEWAY_URI_SAN),
            certificate(dns=GITHUB_PROXY_DNS_SAN, uri=GITHUB_PROXY_URI_SAN),
        )
        self.kubernetes = RuntimeKubernetesProvider(source_client, clock=lambda: NOW)
        self.gitops = RuntimeGitOpsProvider(source_client, github_client, clock=lambda: NOW)
        self.providers = EvidenceProviders(
            kubernetes=self.kubernetes,
            prometheus=RuntimeUnavailablePrometheusProvider(),
            logs=RuntimeUnavailableLogsProvider(),
            gitops=self.gitops,
        )
        self.gateway = RuntimeGateway(token_reviewer=self.reviewer, providers=self.providers, state=self.state)

    def tearDown(self) -> None:
        self.state.close()
        self.temp.cleanup()

    def request(self, *, nonce: str = "runtime-nonce-001", kinds: list[str] | None = None) -> bytes:
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "operation": "collect_staging_frontend_evidence",
                "request_id": f"req-{nonce}",
                "time_range": {"start": "2026-09-21T15:40:00Z", "end": "2026-09-21T16:00:00Z"},
                "evidence_kinds": kinds or ["kubernetes_state", "deployment_revision", "gitops"],
            }
        ).encode()

    def headers(self, nonce: str = "runtime-nonce-001") -> dict[str, str]:
        return {
            "Authorization": "Bearer projected-token",
            "Content-Type": "application/json",
            "X-Request-Nonce": nonce,
        }

    def call(self, **kwargs: Any):
        return self.gateway.handle(
            method="POST",
            path=HTTP_OPERATION_PATH,
            headers=kwargs.get("headers", self.headers()),
            body=kwargs.get("body", self.request()),
            now=kwargs.get("now", NOW),
        )

    def test_end_to_end_uses_only_typed_transports_and_sanitized_durable_audit(self) -> None:
        response = self.call()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["outcome"], "allowed")
        self.assertEqual(self.source.state_calls, 1)
        self.assertGreaterEqual(self.source.revision_calls, 8)
        self.assertEqual(len(self.github_proxy.calls), 7)
        self.assertTrue(all(call["revision"] == REVISION for call in self.github_proxy.calls))
        self.assertTrue(all(call["max_response_bytes"] == 16 * 1024 for call in self.github_proxy.calls))
        stored = self.state.audit_events()[0]
        self.assertEqual(stored["kubernetes_subject"], VALIDATION_KUBERNETES_SUBJECT)
        self.assertNotIn("provider_call_params", stored)
        self.assertNotIn("content", json.dumps(stored))
        self.assertEqual(response.body["audit"]["response_bytes"], len(response.json_bytes()))

    def test_external_auth_mapping_rejects_wrong_audience_or_subject(self) -> None:
        self.reviewer.result = replace(self.reviewer.result, audiences=frozenset({"wrong"}))
        self.assertEqual(self.call().status, 401)
        self.reviewer.result = replace(
            self.reviewer.result,
            audiences=frozenset({EXTERNAL_TOKEN_AUDIENCE}),
            username="system:serviceaccount:other:client",
        )
        self.assertEqual(self.call(headers=self.headers("runtime-nonce-002"), body=self.request(nonce="runtime-nonce-002")).status, 401)
        self.assertEqual(self.source.state_calls, 0)
        self.assertEqual(self.state.audit_events(), [])

    def test_body_auth_and_missing_nonce_are_rejected_before_provider_or_state_use(self) -> None:
        body = json.loads(self.request().decode())
        body["auth"] = {"subject": "forged"}
        self.assertEqual(self.call(body=json.dumps(body).encode()).body["error"]["code"], "body_auth_forbidden")
        self.assertEqual(
            self.call(headers={"Authorization": "Bearer projected-token", "Content-Type": "application/json"}).status,
            401,
        )
        self.assertEqual(self.providers.total_calls(), 0)
        self.assertEqual(self.state.audit_events(), [])

    def test_replay_and_rate_denials_keep_safe_audit_context_and_make_no_provider_calls(self) -> None:
        self.assertEqual(self.call().status, 200)
        before = self.providers.total_calls()
        replay = self.call()
        self.assertEqual(replay.status, 429)
        self.assertEqual(replay.body["audit"]["denial_reason"], "replay_rejected")
        self.assertEqual(replay.body["audit"]["kubernetes_subject"], VALIDATION_KUBERNETES_SUBJECT)
        self.assertEqual(self.providers.total_calls(), before)

        self.state.limits = replace(self.state.limits, max_requests_per_hour=1)
        rate = self.call(headers=self.headers("runtime-nonce-003"), body=self.request(nonce="runtime-nonce-003"))
        self.assertEqual(rate.status, 429)
        self.assertEqual(rate.body["audit"]["denial_reason"], "rate_limited")
        self.assertEqual(self.providers.total_calls(), before)

    def test_unavailable_capabilities_never_call_a_backend(self) -> None:
        for index, kind in enumerate(("kubernetes_events", "logs", "prometheus", "kubernetes_pod_status")):
            nonce = f"unavailable-{index}"
            response = self.call(headers=self.headers(nonce), body=self.request(nonce=nonce, kinds=[kind]))
            self.assertEqual(response.status, 503)
            self.assertEqual(response.body["error"]["code"], "backend_unavailable")
        self.assertEqual(self.source.state_calls, 0)
        self.assertEqual(self.source.revision_calls, 0)
        self.assertEqual(self.github_proxy.calls, [])

    def test_mtls_san_failure_and_github_unavailable_fail_closed(self) -> None:
        self.kubernetes.source.server_certificate = certificate(dns=SOURCE_DNS_SAN, uri="spiffe://wrong")
        response = self.call(body=self.request(kinds=["kubernetes_state"]))
        self.assertEqual(response.status, 503)
        self.assertEqual(self.source.state_calls, 0)

        self.kubernetes.source.server_certificate = certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN)
        self.kubernetes.source.client_certificate = certificate(uri=GATEWAY_URI_SAN, trusted=False)
        response = self.call(headers=self.headers("mtls-ca-001"), body=self.request(nonce="mtls-ca-001", kinds=["kubernetes_state"]))
        self.assertEqual(response.status, 503)
        self.assertEqual(self.source.state_calls, 0)

        self.kubernetes.source.client_certificate = certificate(uri=GATEWAY_URI_SAN)
        self.github_proxy.status = 429
        response = self.call(headers=self.headers("github-429-001"), body=self.request(nonce="github-429-001", kinds=["gitops"]))
        self.assertEqual(response.status, 503)
        self.assertEqual(response.body["error"]["code"], "backend_unavailable")
        self.assertEqual(len(self.github_proxy.calls), 1)

    def test_analysis_runs_are_not_accepted_by_the_runtime_source_contract(self) -> None:
        self.source.state["rollout"]["analysis_runs"] = [
            {"name": "frontend-run", "owner_kind": "Rollout", "owner_name": "frontend"}
        ]
        response = self.call(body=self.request(kinds=["kubernetes_state"]))
        self.assertEqual(response.status, 503)
        self.assertEqual(response.body["error"]["code"], "backend_unavailable")

    def test_runtime_token_config_reserves_only_the_kubernetes_api_audience(self) -> None:
        config = RuntimeTokenConfig()
        self.assertEqual(config.audience, "https://kubernetes.default.svc")
        self.assertEqual(config.path, "/var/run/secrets/evidence-gateway/kubernetes/token")

    def test_github_content_limit_is_enforced_after_a_bounded_envelope_request(self) -> None:
        self.github_proxy.content = "x" * 4097
        response = self.call(body=self.request(kinds=["gitops"]))
        self.assertEqual(response.status, 503)
        self.assertEqual(self.github_proxy.calls[0]["max_response_bytes"], 16 * 1024)

        self.github_proxy.raw_body = b"x" * (16 * 1024 + 1)
        response = self.call(headers=self.headers("github-envelope-001"), body=self.request(nonce="github-envelope-001", kinds=["gitops"]))
        self.assertEqual(response.status, 503)
        self.assertEqual(response.body["error"]["code"], "backend_unavailable")

    def test_private_wsgi_boundary_exposes_only_the_typed_post_operation(self) -> None:
        application = PrivateHTTPApplication(self.gateway)
        result: dict[str, Any] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            result["status"] = status
            result["headers"] = headers

        payload = b"".join(
            application(
                {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": HTTP_OPERATION_PATH,
                    "CONTENT_LENGTH": str(len(self.request(kinds=["kubernetes_state"]))),
                    "wsgi.input": io.BytesIO(self.request(kinds=["kubernetes_state"])),
                    "HTTP_AUTHORIZATION": "Bearer projected-token",
                    "CONTENT_TYPE": "application/json",
                    "HTTP_X_REQUEST_NONCE": "wsgi-nonce-001",
                },
                start_response,
            )
        )
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(json.loads(payload)["outcome"], "allowed")
        self.assertEqual(
            b"".join(application({"REQUEST_METHOD": "GET", "PATH_INFO": HTTP_OPERATION_PATH}, start_response)),
            b'{"error":{"code":"unknown_operation"},"outcome":"denied","schema_version":"evidence-gateway.b1.v1"}',
        )


class RuntimeStateTests(unittest.TestCase):
    def test_recovery_pruning_and_bounded_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            limits = RuntimeStateLimits(max_replay_entries=1, max_rate_subjects=1, max_requests_per_hour=1)
            state = RuntimeState(path, limits)
            state.check_and_record(subject="client", nonce="state-nonce-001", now=NOW, expires_at=NOW + timedelta(seconds=1))
            state.close()
            recovered = RuntimeState(path, limits)
            with self.assertRaisesRegex(Exception, "request nonce"):
                recovered.check_and_record(subject="client", nonce="state-nonce-001", now=NOW, expires_at=NOW + timedelta(seconds=1))
            recovered.check_and_record(
                subject="client", nonce="state-nonce-002", now=NOW + timedelta(hours=2), expires_at=NOW + timedelta(hours=2, seconds=1)
            )
            with self.assertRaisesRegex(Exception, "state is at capacity"):
                recovered.check_and_record(
                    subject="other", nonce="state-nonce-003", now=NOW + timedelta(hours=2), expires_at=NOW + timedelta(hours=3)
                )
            recovered.close()

    def test_audit_is_sanitized_and_capacity_failure_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = RuntimeState(Path(directory) / "state.db", RuntimeStateLimits(max_audit_records=1))
            audit = {
                "request_id": "req-1",
                "decision": "allowed",
                "provider_call_params": [{"token": "not persisted"}],
                "result": {"content": "not persisted"},
            }
            state.write_audit(audit, now=NOW)
            self.assertEqual(state.audit_events(), [{"decision": "allowed", "request_id": "req-1"}])
            with self.assertRaises(RuntimeStateError):
                state.write_audit({"request_id": "req-2", "decision": "allowed"}, now=NOW)
            state.close()

    def test_combined_database_limit_and_invalid_audit_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeStateError):
                RuntimeState(Path(directory) / "too-small.db", RuntimeStateLimits(max_database_bytes=1))
            state = RuntimeState(Path(directory) / "state.db")
            with self.assertRaises(RuntimeStateError):
                state.write_audit({"request_id": "req", "decision": "allowed", "counts": {"value": float("nan")}}, now=NOW)
            state.close()


if __name__ == "__main__":
    unittest.main()
