from __future__ import annotations

import base64
import io
import json
import ssl
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evidence_gateway.providers import EvidenceProviders, ProviderError
from evidence_gateway.runtime.gateway import (
    EXTERNAL_TOKEN_AUDIENCE, HTTP_OPERATION_PATH, VALIDATION_KUBERNETES_SUBJECT, RuntimeGateway,
)
from evidence_gateway.runtime.http_clients import (
    GitHubEgressHTTPClient, KubernetesTokenReviewClient, SourceHTTPClient, TLSConfiguration,
    TOKENREVIEW_HOST, TOKENREVIEW_PATH, SOURCE_STATE_PATH, SOURCE_REVISION_PATH,
)
from evidence_gateway.runtime.state import RuntimeState
from evidence_gateway.runtime.transports import (
    GATEWAY_URI_SAN, SOURCE_DNS_SAN, SOURCE_URI_SAN, GITHUB_PROXY_DNS_SAN, GITHUB_PROXY_URI_SAN,
    GITOPS_PATHS, MTLSSourceClient, MTLSGitHubProxyClient, RuntimeGitOpsProvider,
    RuntimeKubernetesProvider, RuntimeUnavailableLogsProvider, RuntimeUnavailablePrometheusProvider,
)
from tests.test_evidence_gateway_runtime import NOW, REVISION, FakeSource, certificate, nonce


def encoded(value):
    return json.dumps(value).encode()


def token_review():
    return {
        "apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
        "status": {"authenticated": True, "audiences": [EXTERNAL_TOKEN_AUDIENCE],
                   "user": {"username": VALIDATION_KUBERNETES_SUBJECT, "uid": "ignored", "groups": []}},
    }


class Response:
    def __init__(self, body, status=200, headers=None):
        self.status = status
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}
        self.stream = io.BytesIO(body)
        self.reads = []
        self.error = None

    def read(self, size, *, timeout):
        self.reads.append((size, timeout))
        if self.error:
            raise self.error
        return self.stream.read(size)


class Connection:
    def __init__(self, owner, peer):
        self.owner = owner
        self.peer = peer
        self.closed = False

    def peer_certificate(self):
        return self.peer

    def request(self, **kwargs):
        self.owner.requests.append(kwargs)
        if self.owner.request_error:
            raise self.owner.request_error
        return self.owner.response_for(kwargs)

    def close(self):
        self.closed = True


class Executor:
    def __init__(self, response, peer):
        self.response = response
        self.peer = peer
        self.opens = []
        self.requests = []
        self.connections = []
        self.open_error = None
        self.request_error = None
        self.response_for = lambda _: self.response

    def open_tls(self, **kwargs):
        self.opens.append(kwargs)
        if self.open_error:
            raise self.open_error
        connection = Connection(self, self.peer)
        self.connections.append(connection)
        return connection


class HTTPClientTests(unittest.TestCase):
    def setUp(self):
        # Real SSLContext policy, mocked local certificate loading and no sockets.
        self.load_ca = self.enterContext(patch.object(ssl.SSLContext, "load_verify_locations"))
        self.load_chain = self.enterContext(patch.object(ssl.SSLContext, "load_cert_chain"))
        self.network = self.enterContext(patch("socket.socket", side_effect=AssertionError("network forbidden")))
        self.tls = TLSConfiguration("fixture-ca", "fixture-cert", "fixture-key", certificate(uri=GATEWAY_URI_SAN))
        self.executor = Executor(Response(encoded(FakeSource().state)), certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN))
        self.source = SourceHTTPClient(executor=self.executor, tls=self.tls, clock=lambda: NOW)

    def tearDown(self):
        self.network.assert_not_called()

    def review_client(self):
        self.executor.peer = certificate(dns=TOKENREVIEW_HOST, uri="unused")
        self.executor.response = Response(encoded(token_review()), status=201)
        return KubernetesTokenReviewClient(
            executor=self.executor, tls=TLSConfiguration("fixture-api-ca"), clock=lambda: NOW,
            runtime_token_reader=lambda: "fixture-runtime-token",
        )

    def github_client(self):
        self.executor.peer = certificate(dns=GITHUB_PROXY_DNS_SAN, uri=GITHUB_PROXY_URI_SAN)
        return GitHubEgressHTTPClient(executor=self.executor, tls=self.tls, clock=lambda: NOW)

    def github_read(self, client, **overrides):
        values = {"path_id": "stage_values", "revision": REVISION, "max_response_bytes": 16384}
        values.update(overrides)
        return client.read_allowlisted_contents(**values)

    def test_source_exact_operation_and_tls_policy(self):
        self.assertEqual(self.source.get_frontend_state(), FakeSource().state)
        request = self.executor.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], SOURCE_STATE_PATH)
        self.assertEqual(request["body"], b"")
        self.assertNotIn("Authorization", request["headers"])
        opened = self.executor.opens[0]
        self.assertEqual((opened["host"], opened["port"], opened["server_hostname"]),
                         (SOURCE_DNS_SAN, 8443, SOURCE_DNS_SAN))
        context = opened["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertFalse(context.hostname_checks_common_name)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertTrue(0 < opened["timeout"] <= 5)
        self.load_ca.assert_called_once_with(cafile="fixture-ca")
        self.load_chain.assert_called_once_with(certfile="fixture-cert", keyfile="fixture-key")
        self.assertTrue(self.executor.connections[0].closed)

    def test_revision_route_and_projection(self):
        self.executor.response = Response(encoded(FakeSource().application))
        self.assertEqual(self.source.get_deployment_revision(), FakeSource().application)
        self.assertEqual(self.executor.requests[0]["path"], SOURCE_REVISION_PATH)

    def test_tokenreview_fixed_api_body_and_separate_runtime_bearer(self):
        client = self.review_client()
        result = client.review(token="fixture-caller-token", audience=EXTERNAL_TOKEN_AUDIENCE)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.username, VALIDATION_KUBERNETES_SUBJECT)
        request = self.executor.requests[0]
        self.assertEqual((request["method"], request["path"]), ("POST", TOKENREVIEW_PATH))
        self.assertEqual(json.loads(request["body"]), {
            "apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
            "spec": {"token": "fixture-caller-token", "audiences": [EXTERNAL_TOKEN_AUDIENCE]},
        })
        self.assertEqual(request["headers"]["Authorization"], "Bearer fixture-runtime-token")
        self.assertEqual(self.executor.opens[0]["host"], TOKENREVIEW_HOST)
        self.assertEqual(self.executor.opens[0]["port"], 443)
        self.load_chain.assert_not_called()

    def test_tokenreview_input_denial_makes_zero_connection_calls(self):
        client = self.review_client()
        for token, audience in (("valid", "wrong"), ("bad\r\nheader", EXTERNAL_TOKEN_AUDIENCE),
                                ("x" * 8193, EXTERNAL_TOKEN_AUDIENCE), (None, EXTERNAL_TOKEN_AUDIENCE)):
            with self.subTest(token_type=type(token)), self.assertRaises(ProviderError):
                client.review(token=token, audience=audience)
        self.assertEqual(self.executor.opens, [])

    def test_runtime_token_reader_and_api_identity_fail_closed(self):
        client = self.review_client()
        client._runtime_token_reader = lambda: "injected\r\nheader"
        with self.assertRaises(ProviderError):
            client.review(token="fixture", audience=EXTERNAL_TOKEN_AUDIENCE)
        self.assertEqual(self.executor.opens, [])
        client._runtime_token_reader = lambda: "fixture-runtime"
        self.executor.peer = replace(self.executor.peer, dns_names=frozenset({"other"}))
        with self.assertRaises(ProviderError):
            client.review(token="fixture", audience=EXTERNAL_TOKEN_AUDIENCE)
        self.assertEqual(self.executor.requests, [])

    def test_each_endpoint_enforces_its_stream_limit(self):
        review = self.review_client()
        self.executor.response = Response(b" " * 16385, status=201)
        with self.assertRaises(ProviderError):
            review.review(token="fixture", audience=EXTERNAL_TOKEN_AUDIENCE)
        self.assertEqual(self.executor.response.stream.tell(), 16385)
        github = self.github_client()
        self.executor.response = Response(b" " * 16385)
        with self.assertRaises(ProviderError):
            self.github_read(github)
        self.assertEqual(self.executor.response.stream.tell(), 16385)
        self.executor.peer = certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN)
        self.executor.response = Response(b" " * 4097)
        with self.assertRaises(ProviderError):
            self.source.get_deployment_revision()
        self.assertEqual(self.executor.response.stream.tell(), 4097)

    def test_malformed_tokenreview_never_authenticates(self):
        client = self.review_client()
        for mutation in (
            lambda value: value.update(kind="Status"),
            lambda value: value.update(status=[]),
            lambda value: value["status"].update(authenticated="true"),
            lambda value: value["status"].update(audiences=["other"]),
            lambda value: value["status"].update(audiences=[EXTERNAL_TOKEN_AUDIENCE] * 2),
            lambda value: value["status"].update(user={"username": "other"}),
        ):
            value = token_review()
            mutation(value)
            self.executor.response = Response(encoded(value), status=201)
            with self.assertRaises(ProviderError):
                client.review(token="fixture", audience=EXTERNAL_TOKEN_AUDIENCE)
        value = token_review()
        value["status"] = {"authenticated": False, "error": "sensitive backend detail"}
        self.executor.response = Response(encoded(value), status=201)
        self.assertFalse(client.review(token="fixture", audience=EXTERNAL_TOKEN_AUDIENCE).authenticated)

    def test_tls_local_config_failure_prevents_open(self):
        for tls in (replace(self.tls, ca_file=""), replace(self.tls, certificate_file=None),
                    replace(self.tls, client_identity=certificate(uri="wrong"))):
            client = SourceHTTPClient(executor=self.executor, tls=tls, clock=lambda: NOW)
            with self.assertRaises(ProviderError):
                client.get_frontend_state()
        self.load_ca.side_effect = ssl.SSLError("sensitive file detail")
        with self.assertRaisesRegex(ProviderError, "^approved HTTPS transport is unavailable$"):
            self.source.get_frontend_state()
        self.assertEqual(self.executor.opens, [])

    def test_peer_failures_prevent_http_request_and_close_connection(self):
        for peer in (None, {}, replace(self.executor.peer, dns_names=frozenset({"other"})),
                     replace(self.executor.peer, uri_sans=frozenset({"wrong"})),
                     replace(self.executor.peer, extended_key_usages=frozenset({"clientAuth"})),
                     replace(self.executor.peer, trusted_by_configured_ca=False),
                     replace(self.executor.peer, not_after=NOW)):
            self.executor.peer = peer
            with self.subTest(peer=type(peer)), self.assertRaises(ProviderError):
                self.source.get_frontend_state()
        self.assertEqual(self.executor.requests, [])
        self.assertTrue(all(connection.closed for connection in self.executor.connections))

    def test_no_plaintext_redirect_retry_or_error_body_read(self):
        for status in (301, 302, 307, 308, 403, 429, 500):
            self.executor.response = Response(b"sensitive error body", status=status)
            before = len(self.executor.opens)
            with self.assertRaises(ProviderError):
                self.source.get_frontend_state()
            self.assertEqual(len(self.executor.opens), before + 1)
            self.assertEqual(self.executor.response.reads, [])

    def test_timeout_open_request_and_stream_errors_are_safe(self):
        for stage in ("open", "request", "read"):
            self.executor.open_error = None
            self.executor.request_error = None
            self.executor.response.error = None
            error = TimeoutError("sensitive backend address")
            if stage == "read":
                self.executor.response.error = error
            else:
                setattr(self.executor, stage + "_error", error)
            with self.assertRaisesRegex(ProviderError, "^approved HTTPS transport is unavailable$"):
                self.source.get_frontend_state()
        self.assertTrue(all(connection.closed for connection in self.executor.connections))

    def test_streamed_limit_without_or_with_lying_content_length(self):
        for length in (None, "1", "16384"):
            headers = {"Content-Type": "application/json"}
            if length is not None:
                headers["Content-Length"] = length
            self.executor.response = Response(b" " * 16385, headers=headers)
            with self.assertRaises(ProviderError):
                self.source.get_frontend_state()
            self.assertEqual(self.executor.response.stream.tell(), 16385)
            self.assertTrue(all(size <= 4096 for size, _ in self.executor.response.reads))
        self.executor.response = Response(b"", headers={"Content-Type": "application/json", "Content-Length": "16385"})
        with self.assertRaises(ProviderError):
            self.source.get_frontend_state()
        self.assertEqual(self.executor.response.reads, [])

    def test_stream_deadline_and_oversized_chunk_fail_closed(self):
        ticks = iter([0, 0, 0, 0, 6])
        client = SourceHTTPClient(executor=self.executor, tls=self.tls, clock=lambda: NOW, monotonic=lambda: next(ticks))
        with self.assertRaises(ProviderError):
            client.get_frontend_state()
        self.executor.response.read = lambda size, timeout: b"x" * (size + 1)
        with self.assertRaises(ProviderError):
            self.source.get_frontend_state()

    def test_response_headers_and_json_are_strict(self):
        for body in (b"[]", b"null", b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff", b"[" * 1200):
            self.executor.response = Response(body)
            with self.assertRaises(ProviderError):
                self.source.get_frontend_state()
        for headers in ({}, {"Content-Type": "text/plain"},
                        {"Content-Type": "application/json", "Content-Encoding": "gzip"},
                        {"Content-Type": "application/json", "Content-Length": "-1"},
                        {"Content-Type": "application/json", "content-type": "application/json"}):
            self.executor.response = Response(encoded(FakeSource().state), headers=headers)
            with self.assertRaises(ProviderError):
                self.source.get_frontend_state()

    def test_source_rejects_raw_extra_scope_and_malformed_fields(self):
        for mutate in (
            lambda value: value.update(metadata={"secret": "private"}),
            lambda value: value["workload"].update(namespace="online-shop-dev"),
            lambda value: value["workload"].update(ready_replicas=True),
            lambda value: value["workload"].update(conditions=[{}] * 9),
            lambda value: value["rollout"].update(phase=[]),
            lambda value: value["rollout"].update(analysis_runs=[{}]),
            lambda value: value["ingress"].update(paths=["/stage-private"]),
        ):
            value = deepcopy(FakeSource().state)
            mutate(value)
            self.executor.response = Response(encoded(value))
            with self.assertRaises(ProviderError):
                self.source.get_frontend_state()

    def test_revision_rejects_branch_wrong_application_and_extra_fields(self):
        for changes in ({"revision": "main"}, {"application": "other"}, {"health_status": None}, {"raw": {}}):
            self.executor.response = Response(encoded({**FakeSource().application, **changes}))
            with self.assertRaises(ProviderError):
                self.source.get_deployment_revision()

    def test_github_fixed_mapping_and_decoded_projection(self):
        client = self.github_client()
        for path_id, path in GITOPS_PATHS.items():
            envelope = {"type": "file", "path": path, "encoding": "base64",
                        "content": "a2luZDogQ29uZmlnTWFwCg==\n", "download_url": "ignored", "_links": {}}
            self.executor.response = Response(encoded(envelope))
            result = self.github_read(client, path_id=path_id)
            request = self.executor.requests[-1]
            self.assertEqual(request["path"], f"/repos/DimitryZH/sre-platform/contents/{path}?ref={REVISION}")
            self.assertEqual(request["method"], "GET")
            self.assertEqual(self.executor.opens[-1]["host"], GITHUB_PROXY_DNS_SAN)
            self.assertNotIn("Authorization", request["headers"])
            self.assertEqual(set(json.loads(result.body)), {"type", "path", "encoding", "content"})
            self.assertEqual(base64.b64decode(json.loads(result.body)["content"]), b"kind: ConfigMap\n")

    def test_github_rejects_broad_paths_refs_limits_before_open(self):
        client = self.github_client()
        for changes in ({"path_id": "../secret"}, {"path_id": "*"}, {"path_id": []},
                        {"revision": "main"}, {"revision": REVISION + "&other=x"},
                        {"max_response_bytes": 16385}, {"max_response_bytes": True}, {"max_response_bytes": 0}):
            with self.assertRaises(ProviderError):
                self.github_read(client, **changes)
        self.assertEqual(self.executor.opens, [])

    def test_github_status_and_contents_fail_closed(self):
        client = self.github_client()
        for status in (403, 429):
            self.executor.response = Response(b"sensitive", status=status)
            with self.assertRaises(ProviderError):
                self.github_read(client)
            self.assertEqual(self.executor.response.reads, [])
        for changes in ({"type": "dir"}, {"path": "README.md"}, {"encoding": "none"},
                        {"content": "invalid!"}, {"content": base64.b64encode(b"x" * 4097).decode()}):
            self.executor.response = Response(encoded({
                "type": "file", "path": GITOPS_PATHS["stage_values"], "encoding": "base64", "content": "", **changes,
            }))
            with self.assertRaises(ProviderError):
                self.github_read(client)

    def test_runtime_end_to_end_with_concrete_clients_and_policy_denials(self):
        review = self.review_client()
        review_executor = self.executor
        source_executor = Executor(Response(b""), certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN))
        source_executor.response_for = lambda request: Response(encoded(
            FakeSource().state if request["path"] == SOURCE_STATE_PATH else FakeSource().application
        ))
        source = MTLSSourceClient(
            SourceHTTPClient(executor=source_executor, tls=self.tls, clock=lambda: NOW),
            certificate(uri=GATEWAY_URI_SAN), certificate(dns=SOURCE_DNS_SAN, uri=SOURCE_URI_SAN),
        )
        github_executor = Executor(Response(b""), certificate(dns=GITHUB_PROXY_DNS_SAN, uri=GITHUB_PROXY_URI_SAN))
        github_executor.response_for = lambda request: Response(encoded({
            "type": "file", "encoding": "base64", "content": "",
            "path": request["path"].split("/contents/")[1].split("?ref=")[0],
        }))
        github = MTLSGitHubProxyClient(
            GitHubEgressHTTPClient(executor=github_executor, tls=self.tls, clock=lambda: NOW),
            certificate(uri=GATEWAY_URI_SAN), certificate(dns=GITHUB_PROXY_DNS_SAN, uri=GITHUB_PROXY_URI_SAN),
        )
        providers = EvidenceProviders(RuntimeKubernetesProvider(source, clock=lambda: NOW),
                                      RuntimeUnavailablePrometheusProvider(), RuntimeUnavailableLogsProvider(),
                                      RuntimeGitOpsProvider(source, github, clock=lambda: NOW))
        with tempfile.TemporaryDirectory() as directory:
            state = RuntimeState(Path(directory) / "state.db")
            try:
                gateway = RuntimeGateway(token_reviewer=review, providers=providers, state=state)
                request = {"schema_version": "evidence-gateway.b1.v1", "operation": "collect_staging_frontend_evidence",
                           "request_id": "transport-test", "time_range": {"start": "2026-09-21T15:40:00Z", "end": "2026-09-21T16:00:00Z"},
                           "evidence_kinds": ["kubernetes_state", "deployment_revision", "gitops"]}

                def call(value, label):
                    review_executor.response = Response(encoded(token_review()), status=201)
                    return gateway.handle(method="POST", path=HTTP_OPERATION_PATH, body=encoded(value), now=NOW,
                                          headers={"Authorization": "Bearer fixture", "Content-Type": "application/json",
                                                   "X-Request-Nonce": nonce(label)})

                response = call(request, "positive")
                self.assertEqual(response.status, 200)
                self.assertEqual(len(github_executor.requests), 7)
                self.assertEqual(len(source_executor.requests), 9)
                before = (len(source_executor.opens), len(github_executor.opens))
                for index, field in enumerate(("selector", "url", "promql", "ref", "namespace")):
                    self.assertEqual(call({**request, field: "unapproved"}, f"denial-{index}").status, 400)
                self.assertEqual(before, (len(source_executor.opens), len(github_executor.opens)))
                before_review = len(review_executor.opens)
                denied = gateway.handle(method="POST", path=HTTP_OPERATION_PATH, body=encoded(request),
                                        headers={"Content-Type": "application/json"}, now=NOW)
                self.assertEqual(denied.status, 401)
                self.assertEqual(len(review_executor.opens), before_review)
                self.assertEqual(before, (len(source_executor.opens), len(github_executor.opens)))
            finally:
                state.close()


if __name__ == "__main__":
    unittest.main()
