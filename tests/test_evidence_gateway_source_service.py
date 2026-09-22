from __future__ import annotations

import json
import ssl
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from evidence_gateway.adapters import ArgoCDGitOpsAdapter, KubernetesEvidenceAdapter
from evidence_gateway.providers import EvidenceProviders, ProviderError
from evidence_gateway.runtime.gateway import HTTP_OPERATION_PATH, RuntimeGateway
from evidence_gateway.runtime.http_clients import SourceHTTPClient, TLSConfiguration
from evidence_gateway.runtime.source_contract import (
    SOURCE_STATE_PATH, SOURCE_REVISION_PATH, SOURCE_STATE_MAX_BYTES, SOURCE_REVISION_MAX_BYTES,
)
from evidence_gateway.runtime.source_service import PrivateSourceService
from evidence_gateway.runtime.state import RuntimeState
from evidence_gateway.runtime.transports import (
    GATEWAY_URI_SAN, SOURCE_DNS_SAN, SOURCE_URI_SAN, GITHUB_PROXY_DNS_SAN, GITHUB_PROXY_URI_SAN,
    MTLSSourceClient, MTLSGitHubProxyClient, RuntimeGitOpsProvider, RuntimeKubernetesProvider,
    RuntimeUnavailableLogsProvider, RuntimeUnavailablePrometheusProvider,
)
from tests.test_evidence_gateway_b2 import (
    RecordingKubernetesBackend, RecordingArgoCDBackend, RecordingGitOpsBackend,
)
from tests.test_evidence_gateway_http_clients import Executor, Response
from tests.test_evidence_gateway_runtime import (
    NOW, FakeGitHubProxy, FakeTokenReviewer, certificate, nonce,
)


class PrivateSourceServiceTests(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch("socket.socket", side_effect=AssertionError("network forbidden")))
        self.backend = RecordingKubernetesBackend()
        self.backend.rollout["analysis_runs"] = []
        self.ingress = self.backend.get_ingress()
        self.backend.calls.clear()
        self.argocd = RecordingArgoCDBackend()
        self.git_backend = RecordingGitOpsBackend()
        self.kubernetes = KubernetesEvidenceAdapter(self.backend)
        self.gitops = ArgoCDGitOpsAdapter(self.argocd, self.git_backend)
        self.peer = certificate(uri=GATEWAY_URI_SAN)
        self.service = PrivateSourceService(kubernetes=self.kubernetes, gitops=self.gitops, clock=lambda: NOW)
        self.forbidden = []
        for name in ("get_deployment", "list_pods", "list_events", "get_analysis_runs",
                     "read_frontend_container_logs", "query_recording_rule"):
            method = Mock(side_effect=AssertionError("unsupported backend operation"))
            self.enterContext(patch.object(self.backend, name, method, create=True))
            self.forbidden.append(method)

    def tearDown(self):
        self.network.assert_not_called()
        for method in self.forbidden:
            method.assert_not_called()
        self.assertEqual(self.git_backend.calls, [])

    def call(self, **overrides):
        request = {"method": "GET", "path": SOURCE_STATE_PATH, "body": b"",
                   "headers": {"Host": SOURCE_DNS_SAN}, "peer": self.peer}
        request.update(overrides)
        return self.service.handle(**request)

    def assert_no_backends(self):
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.argocd.calls, [])
        self.assertEqual(self.kubernetes.calls.records, [])
        self.assertEqual(self.gitops.calls.records, [])

    def assert_unavailable(self, response):
        self.assertEqual(response.status, 503)
        self.assertEqual(response.body, {"outcome": "denied", "error": {"code": "backend_unavailable"}})
        self.assertNotIn("sensitive", response.json_bytes().decode())

    def test_state_reads_only_exact_rollout_and_ingress_with_projected_output(self):
        self.backend.rollout["metadata"] = {"uid": "sensitive", "annotations": {"private": "sensitive"}}
        response = self.call()
        self.assertEqual(response.status, 200)
        self.assertEqual(self.backend.calls, [
            {"operation": "get_rollout", "namespace": "online-shop-stage", "name": "frontend"},
            {"operation": "get_ingress", "namespace": "online-shop-stage", "name": "online-shop-frontend"},
        ])
        self.assertEqual(self.argocd.calls, [])
        self.assertEqual(set(response.body), {"workload", "rollout", "ingress"})
        self.assertEqual(set(response.body["workload"]), {
            "name", "namespace", "ready_replicas", "desired_replicas", "available_replicas", "conditions",
        })
        self.assertEqual(response.body["rollout"]["analysis_runs"], [])
        self.assertEqual(response.body["ingress"], self.ingress)
        self.assertNotIn("sensitive", response.json_bytes().decode())
        self.assertLessEqual(len(response.json_bytes()), SOURCE_STATE_MAX_BYTES)
        self.backend.rollout["conditions"][0]["reason"] = "Changed"
        self.assertEqual(response.body["workload"]["conditions"][0]["reason"], "MinimumReplicasAvailable")

    def test_revision_reads_only_exact_application_and_projects_immutable_sha(self):
        self.argocd.application["metadata"]["uid"] = "sensitive"
        response = self.call(path=SOURCE_REVISION_PATH)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, {"application": "online-shop-stage", "revision": "b" * 40,
                                         "sync_status": "Synced", "health_status": "Healthy"})
        self.assertEqual(self.argocd.calls, [{"name": "online-shop-stage"}])
        self.assertEqual(self.backend.calls, [])
        self.assertLessEqual(len(response.json_bytes()), SOURCE_REVISION_MAX_BYTES)

    def test_revision_is_resolved_each_request_without_shared_binding(self):
        first = self.call(path=SOURCE_REVISION_PATH)
        self.argocd.application["status"]["sync"]["revision"] = "c" * 40
        second = self.call(path=SOURCE_REVISION_PATH)
        self.assertEqual(first.body["revision"], "b" * 40)
        self.assertEqual(second.body["revision"], "c" * 40)
        self.assertEqual(len(self.argocd.calls), 2)

    def test_unknown_methods_and_paths_fail_before_adapters(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "get", None, []):
            with self.subTest(method=method):
                self.assertEqual(self.call(method=method).status, 404)
        for path in (None, [], "/", SOURCE_STATE_PATH + "/", SOURCE_STATE_PATH + "?",
                     SOURCE_STATE_PATH + "#fragment", SOURCE_STATE_PATH.replace("frontend", "cart"),
                     SOURCE_STATE_PATH.replace("staging", "production"),
                     SOURCE_STATE_PATH.replace("frontend", "%66rontend"),
                     SOURCE_STATE_PATH.replace("/state", "/../frontend/state"),
                     "https://" + SOURCE_DNS_SAN + SOURCE_STATE_PATH):
            with self.subTest(path=path):
                self.assertEqual(self.call(path=path).status, 404)
        self.assert_no_backends()

    def test_unexposed_capabilities_and_caller_parameters_have_zero_calls(self):
        for capability in ("events", "logs", "pod-status", "pods", "prometheus", "analysis-runs", "gitops"):
            self.assertEqual(self.call(path=SOURCE_STATE_PATH.removesuffix("state") + capability).status, 404)
        for route in (SOURCE_STATE_PATH, SOURCE_REVISION_PATH):
            for parameter in ("resource=secrets", "selector=app%3Dcart", "namespace=default", "path=README.md",
                              "ref=main", "query=up", "url=https://other", "limit=999", "start=now"):
                self.assertEqual(self.call(path=route + "?" + parameter).status, 404)
        self.assert_no_backends()

    def test_any_body_including_auth_and_malformed_json_is_rejected_without_calls(self):
        for route in (SOURCE_STATE_PATH, SOURCE_REVISION_PATH):
            for body in (b"{}", b'{"auth":{"subject":"gateway"}}', b"{", b" ", b"\x00", b"x" * 65536,
                         "", None, {}, bytearray()):
                with self.subTest(route=route, body_type=type(body)):
                    self.assertEqual(self.call(path=route, body=body).status, 400)
        self.assert_no_backends()

    def test_headers_cannot_supply_identity_or_other_inputs(self):
        for key in ("Authorization", "X-Client-Cert", "X-Forwarded-Client-Cert", "X-Gateway-Identity",
                    "X-Remote-User", "X-Original-URL", "X-Forwarded-Uri", "X-Resource", "X-Selector",
                    "X-Query", "X-Ref", "X-HTTP-Method-Override"):
            headers = {"Host": SOURCE_DNS_SAN, key: GATEWAY_URI_SAN}
            self.assertEqual(self.call(headers=headers).status, 400)
            self.assertEqual(self.call(headers=headers, peer=None).status, 401)
        self.assert_no_backends()

    def test_malformed_or_ambiguous_headers_fail_before_adapters(self):
        for headers in (None, [], {}, {"Host": "other"}, {"Host": SOURCE_DNS_SAN, "host": SOURCE_DNS_SAN},
                        {"Host": SOURCE_DNS_SAN, "Content-Length": "1"},
                        {"Host": SOURCE_DNS_SAN, "Content-Length": "00"},
                        {"Host": SOURCE_DNS_SAN, "Transfer-Encoding": "chunked"},
                        {"Host": SOURCE_DNS_SAN, "Accept-Encoding": "gzip"},
                        {"Host": SOURCE_DNS_SAN, "Accept": "text/html"},
                        {"Host": SOURCE_DNS_SAN, "Content-Length": 0},
                        {"Host": SOURCE_DNS_SAN, 1: "bad"},
                        {"Host": SOURCE_DNS_SAN, "x" * 1000: "bad"},
                        {"Host": SOURCE_DNS_SAN, "Accept": "x" * 1000},
                        {"Host": SOURCE_DNS_SAN + "\r\n"}):
            with self.subTest(headers_type=type(headers)):
                self.assertEqual(self.call(headers=headers).status, 400)
        self.assert_no_backends()

    def test_client_header_contract_with_explicit_zero_body_length_is_allowed(self):
        response = self.call(headers={"host": SOURCE_DNS_SAN + ":8443", "Accept": "application/json",
                                      "Accept-Encoding": "identity", "Content-Length": "0"})
        self.assertEqual(response.status, 200)

    def test_peer_requires_trusted_exact_gateway_uri_and_client_eku(self):
        for peer in (None, {}, self.peer.__dict__,
                     replace(self.peer, trusted_by_configured_ca=False),
                     replace(self.peer, trusted_by_configured_ca=1),
                     replace(self.peer, uri_sans=frozenset({SOURCE_URI_SAN})),
                     replace(self.peer, uri_sans=frozenset({GATEWAY_URI_SAN, "other"})),
                     replace(self.peer, uri_sans={GATEWAY_URI_SAN}),
                     replace(self.peer, dns_names=frozenset({None})),
                     replace(self.peer, extended_key_usages=frozenset()),
                     replace(self.peer, extended_key_usages=frozenset({"serverAuth"})),
                     replace(self.peer, extended_key_usages=frozenset({"serverAuth", "clientAuth"}))):
            for route in (SOURCE_STATE_PATH, SOURCE_REVISION_PATH):
                self.assertEqual(self.call(path=route, peer=peer).status, 401)
        self.assert_no_backends()

    def test_peer_validity_and_shapes_fail_closed_at_full_precision(self):
        for peer in (replace(self.peer, not_before=NOW + timedelta(microseconds=1)),
                     replace(self.peer, not_after=NOW),
                     replace(self.peer, not_after=NOW - timedelta(microseconds=1)),
                     replace(self.peer, not_before=NOW.replace(tzinfo=None)),
                     replace(self.peer, not_after="tomorrow"),
                     replace(self.peer, not_before=None)):
            self.assertEqual(self.call(peer=peer).status, 401)
        self.assert_no_backends()
        self.assertEqual(self.call(peer=replace(self.peer, not_before=NOW,
                                               not_after=NOW + timedelta(microseconds=1))).status, 200)

    def test_backend_failures_are_safe_without_other_backend_reads(self):
        for operation, owner, route in (("get_rollout", self.backend, SOURCE_STATE_PATH),
                                        ("get_ingress", self.backend, SOURCE_STATE_PATH),
                                        ("get_application", self.argocd, SOURCE_REVISION_PATH)):
            for error in (ProviderError("sensitive token=fixture"), TimeoutError("sensitive address"),
                          ValueError("sensitive payload")):
                with patch.object(owner, operation, side_effect=error):
                    self.assert_unavailable(self.call(path=route))
        self.assertFalse(any(call["operation"] != "get_rollout" for call in self.backend.calls))

    def test_malformed_adapter_collections_and_objects_are_never_serialized(self):
        for method, adapter, route in (("get_frontend_state", self.kubernetes, SOURCE_STATE_PATH),
                                       ("get_deployment_revision", self.gitops, SOURCE_REVISION_PATH)):
            for value in (None, [], "sensitive", 1, {}, {"raw": "sensitive"}, iter(["sensitive"])):
                with patch.object(adapter, method, return_value=value):
                    self.assert_unavailable(self.call(path=route))
        self.assert_no_backends()

    def test_malformed_and_out_of_scope_rollout_fields_fail_closed(self):
        original = deepcopy(self.backend.rollout)
        for changes in ({"name": "cart"}, {"namespace": "online-shop-dev"}, {"ready_replicas": True},
                        {"desired_replicas": -1}, {"available_replicas": 1001}, {"current_step": float("nan")},
                        {"ready_replicas": float("inf")}, {"phase": []}, {"phase": "token=fixture"},
                        {"phase": "line\nbreak"}, {"phase": "x" * 65}, {"stable_service": "cart"},
                        {"canary_service": "cart"}, {"conditions": [None]},
                        {"conditions": [{"type": "Ready", "status": True, "reason": "Healthy"}]},
                        {"conditions": [{"type": "Ready", "status": "True", "reason": "Healthy", "raw": {}}]}):
            self.backend.rollout = {**deepcopy(original), **changes}
            self.assert_unavailable(self.call())

    def test_analysis_runs_and_oversized_conditions_are_not_accepted(self):
        for changes in ({"analysis_runs": [{"owner_kind": "Rollout", "owner_name": "frontend"}]},
                        {"analysis_runs": None}, {"analysis_runs": [{}] * 9},
                        {"conditions": [{}] * 9}, {"conditions": None}):
            with patch.object(self.backend, "rollout", {**self.backend.rollout, **changes}):
                self.assert_unavailable(self.call())

    def test_ingress_requires_exact_target_path_and_strict_safe_projection(self):
        for changes in ({"name": "cart"}, {"namespace": "default"}, {"paths": ["/stage-private"]},
                        {"paths": ["/stage", "/private"]}, {"paths": "/stage"}, {"class_name": None},
                        {"class_name": "10.1.2.3"}, {"metadata": {"uid": "sensitive"}}):
            with patch.object(self.backend, "get_ingress", return_value={**self.ingress, **changes}):
                self.assert_unavailable(self.call())

    def test_revision_rejects_wrong_application_mutable_refs_and_malformed_status(self):
        for mutation in (
            lambda value: value["metadata"].update(name="other"),
            lambda value: value["status"]["sync"].update(revision="main"),
            lambda value: value["status"]["sync"].update(revision="B" * 40),
            lambda value: value["status"]["sync"].update(revision="b" * 40 + "?ref=main"),
            lambda value: value["status"]["sync"].update(status=None),
            lambda value: value["status"]["health"].update(status="token=fixture"),
            lambda value: value["status"].update(sync=[]),
            lambda value: value.update(metadata=None),
        ):
            value = deepcopy(self.argocd.application)
            mutation(value)
            with patch.object(self.argocd, "application", value):
                self.assert_unavailable(self.call(path=SOURCE_REVISION_PATH))

    def test_malformed_backend_records_are_safe(self):
        for operation, owner, route in (("get_rollout", self.backend, SOURCE_STATE_PATH),
                                        ("get_ingress", self.backend, SOURCE_STATE_PATH),
                                        ("get_application", self.argocd, SOURCE_REVISION_PATH)):
            for value in (None, [], {}, "sensitive"):
                with patch.object(owner, operation, return_value=value):
                    self.assert_unavailable(self.call(path=route))

    def test_response_limit_applies_to_exact_serialized_bytes(self):
        for route, constant in ((SOURCE_STATE_PATH, "SOURCE_STATE_MAX_BYTES"),
                                (SOURCE_REVISION_PATH, "SOURCE_REVISION_MAX_BYTES")):
            size = len(self.call(path=route).json_bytes())
            with patch("evidence_gateway.runtime.source_service." + constant, size):
                self.assertEqual(len(self.call(path=route).json_bytes()), size)
                self.assertEqual(self.call(path=route).status, 200)
            with patch("evidence_gateway.runtime.source_service." + constant, size - 1):
                self.assert_unavailable(self.call(path=route))
        self.backend.rollout["conditions"] = [{"type": "\U00010000" * 64, "status": "\U00010000" * 32,
                                                "reason": "\U00010000" * 128}] * 8
        self.assert_unavailable(self.call())

    def make_client(self):
        self.enterContext(patch.object(ssl.SSLContext, "load_verify_locations"))
        self.enterContext(patch.object(ssl.SSLContext, "load_cert_chain"))
        self.executor = Executor(Response(b""), certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN))

        def dispatch(request):
            # Trusted peer is connection metadata, never an HTTP input.
            reply = self.service.handle(method=request["method"], path=request["path"],
                                        headers=request["headers"], body=request["body"], peer=self.peer)
            payload = reply.json_bytes()
            self.last_transport_response = Response(payload, reply.status, {"Content-Type": "application/json",
                                                                            "Content-Length": str(len(payload))})
            return self.last_transport_response

        self.executor.response_for = dispatch
        return SourceHTTPClient(executor=self.executor,
                                tls=TLSConfiguration("fixture-ca", "fixture-cert", "fixture-key", self.peer),
                                clock=lambda: NOW)

    def test_end_to_end_client_and_b2_adapters_for_both_routes(self):
        client = self.make_client()
        self.assertEqual(client.get_frontend_state()["ingress"], self.ingress)
        self.assertEqual(client.get_deployment_revision()["revision"], "b" * 40)
        self.assertEqual([request["path"] for request in self.executor.requests],
                         [SOURCE_STATE_PATH, SOURCE_REVISION_PATH])
        self.assertTrue(all(connection.closed for connection in self.executor.connections))
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(self.argocd.calls, [{"name": "online-shop-stage"}])

    def test_client_maps_service_denials_without_reading_error_payload(self):
        client = self.make_client()
        self.peer = replace(self.peer, uri_sans=frozenset({"other"}))
        with self.assertRaisesRegex(ProviderError, "^approved HTTPS transport is unavailable$"):
            client.get_frontend_state()
        self.assertEqual(self.last_transport_response.reads, [])
        self.assert_no_backends()
        self.peer = certificate(uri=GATEWAY_URI_SAN)
        self.backend.rollout["phase"] = None
        with self.assertRaisesRegex(ProviderError, "^approved HTTPS transport is unavailable$"):
            client.get_frontend_state()
        self.assertEqual(self.last_transport_response.reads, [])

    def test_runtime_collection_end_to_end_with_bounded_source_evidence(self):
        source = MTLSSourceClient(self.make_client(), self.peer, certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN))
        github_transport = FakeGitHubProxy()
        github = MTLSGitHubProxyClient(github_transport, self.peer,
                                      certificate(dns=GITHUB_PROXY_DNS_SAN, uri=GITHUB_PROXY_URI_SAN))
        providers = EvidenceProviders(RuntimeKubernetesProvider(source, clock=lambda: NOW),
                                      RuntimeUnavailablePrometheusProvider(), RuntimeUnavailableLogsProvider(),
                                      RuntimeGitOpsProvider(source, github, clock=lambda: NOW))
        with tempfile.TemporaryDirectory() as directory:
            state = RuntimeState(Path(directory) / "audit.db")
            try:
                runtime = RuntimeGateway(token_reviewer=FakeTokenReviewer(), providers=providers, state=state)
                request = {"schema_version": "evidence-gateway.b1.v1", "operation": "collect_staging_frontend_evidence",
                           "request_id": "source-service-test", "time_range": {
                               "start": "2026-09-21T15:40:00Z", "end": "2026-09-21T16:00:00Z"},
                           "evidence_kinds": ["kubernetes_state", "deployment_revision"]}
                reply = runtime.handle(method="POST", path=HTTP_OPERATION_PATH, body=json.dumps(request).encode(),
                                       headers={"Authorization": "Bearer fixture", "Content-Type": "application/json",
                                                "X-Request-Nonce": nonce("source-service")}, now=NOW)
                self.assertEqual(reply.status, 200)
                self.assertEqual(reply.body["outcome"], "allowed")
                self.assertRegex(reply.body["evidence_id"], r"^[a-f0-9]{64}$")
                self.assertRegex(reply.body["request_fingerprint"], r"^[a-f0-9]{64}$")
                self.assertEqual(reply.body["result"]["source_revision"], "b" * 40)
                self.assertEqual(reply.body["audit"]["revision"], "b" * 40)
                self.assertEqual(reply.body["audit"]["response_bytes"], len(reply.json_bytes()))
                self.assertEqual(len(self.backend.calls), 2)
                self.assertEqual(self.argocd.calls, [{"name": "online-shop-stage"}])
                self.assertEqual(github_transport.calls, [])
            finally:
                state.close()


if __name__ == "__main__":
    unittest.main()
