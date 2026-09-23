from __future__ import annotations

import base64
import io
import ssl
import tempfile
import unittest
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from evidence_gateway.canonical import canonical_json
from evidence_gateway.policy import SCHEMA_VERSION
from evidence_gateway.providers import ProviderError
from evidence_gateway.runtime.assembly import (
    GATEWAY_ROLE, SOURCE_ROLE, BuiltRuntime, RuntimeAssemblyError, RuntimeConfig,
    _source_peer, build_runtime, serve,
)
from evidence_gateway.runtime.gateway import EXTERNAL_TOKEN_AUDIENCE, HTTP_OPERATION_PATH, VALIDATION_KUBERNETES_SUBJECT
from evidence_gateway.runtime.http_clients import (
    APPLICATION_PATH, INGRESS_PATH, ROLLOUT_PATH, SOURCE_STATE_PATH,
)
from evidence_gateway.runtime.kubernetes_backends import APPROVED_INGRESS_API_PATH
from evidence_gateway.runtime.source_contract import SOURCE_REVISION_PATH
from evidence_gateway.runtime.tls_runtime import certificate_from_file
from evidence_gateway.runtime.transports import (
    GATEWAY_DNS_SAN, GATEWAY_URI_SAN, SOURCE_DNS_SAN, SOURCE_URI_SAN, PeerCertificate,
)


class Response:
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {"Content-Type": "application/json", "Content-Length": str(len(body))}

    def read(self, size: int, *, timeout: float) -> bytes:
        return self.body.read(size)


class Connection:
    def __init__(self, executor: Executor, host: str) -> None:
        self.executor = executor
        self.host = host
        self.closed = False

    def peer_certificate(self) -> PeerCertificate:
        return self.executor.peers[self.host]

    def request(self, **request):
        self.executor.requests.append({"host": self.host, **request})
        return self.executor.respond(self.host, request)

    def close(self) -> None:
        self.closed = True


class Executor:
    def __init__(self, peers: dict[str, PeerCertificate], responder) -> None:
        self.peers = peers
        self.responder = responder
        self.opens: list[dict] = []
        self.requests: list[dict] = []
        self.connections: list[Connection] = []

    def open_tls(self, **values):
        self.opens.append(values)
        connection = Connection(self, values["host"])
        self.connections.append(connection)
        return connection

    def respond(self, host: str, request: dict):
        return self.responder(host, request)


class CertificateFixture:
    def __init__(self, directory: Path) -> None:
        self.now = datetime.now(UTC)
        self.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ca = self._ca("evidence-test-ca")
        self.ca_file = directory / "ca.pem"
        self.ca_file.write_bytes(self.ca.public_bytes(serialization.Encoding.PEM))
        self.kubernetes_ca_file = directory / "kubernetes-ca.pem"
        self.kubernetes_ca_file.write_bytes(self.ca.public_bytes(serialization.Encoding.PEM))
        self.token_file = directory / "token"
        self.token_file.write_text("fixture-runtime-token\n", encoding="ascii")

    def issue(
        self, directory: Path, name: str, *, dns: tuple[str, ...] = (), uris: tuple[str, ...] = (),
        usages: tuple[x509.ObjectIdentifier, ...] = (ExtendedKeyUsageOID.CLIENT_AUTH,),
        not_before: datetime | None = None, not_after: datetime | None = None,
    ) -> tuple[Path, Path]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        san = [*(x509.DNSName(value) for value in dns), *(x509.UniformResourceIdentifier(value) for value in uris)]
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(self.ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before or self.now - timedelta(minutes=5))
            .not_valid_after(not_after or self.now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.ExtendedKeyUsage(list(usages)), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )
        cert_file = directory / f"{name}.pem"
        key_file = directory / f"{name}.key"
        cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        return cert_file, key_file

    def _ca(self, name: str) -> x509.Certificate:
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        return (
            x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject).public_key(self.ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(self.now - timedelta(days=1)).not_valid_after(self.now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )


def encoded(value) -> bytes:
    return canonical_json(value).encode("utf-8")


def peer(*, dns=(), uris=(), usages=("serverAuth",), now: datetime | None = None) -> PeerCertificate:
    current = now or datetime.now(UTC)
    return PeerCertificate(True, frozenset(dns), frozenset(uris), frozenset(usages),
                           current - timedelta(minutes=5), current + timedelta(minutes=5))


def rollout() -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout",
        "metadata": {"name": "frontend", "namespace": "online-shop-stage", "uid": "discarded"},
        "spec": {"replicas": 1, "strategy": {"canary": {
            "stableService": "frontend", "canaryService": "frontend-canary", "steps": [{"setWeight": 100}],
        }}},
        "status": {"readyReplicas": 1, "availableReplicas": 1, "phase": "Healthy", "currentStepIndex": 7,
                   "conditions": [{"type": "Completed", "status": "True", "reason": "RolloutCompleted",
                                   "message": "discarded"}]},
    }


def ingress() -> dict:
    return {
        "apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
        "metadata": {"name": "online-shop-frontend", "namespace": "online-shop-stage"},
        "spec": {"ingressClassName": "nginx", "rules": [{"http": {"paths": [{
            "path": APPROVED_INGRESS_API_PATH, "pathType": "ImplementationSpecific",
            "backend": {"service": {"name": "frontend", "port": {"name": "http"}}},
        }]}}]},
    }


def application() -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": "online-shop-stage", "namespace": "argocd"},
        "status": {"sync": {"revision": "b" * 40, "status": "Synced"}, "health": {"status": "Healthy"}},
    }


def projected_state() -> dict:
    return {
        "workload": {"name": "frontend", "namespace": "online-shop-stage", "ready_replicas": 1,
                     "desired_replicas": 1, "available_replicas": 1,
                     "conditions": [{"type": "Completed", "status": "True", "reason": "RolloutCompleted"}]},
        "rollout": {"name": "frontend", "namespace": "online-shop-stage", "phase": "Healthy",
                    "current_step": 7, "stable_service": "frontend", "canary_service": "frontend-canary",
                    "analysis_runs": []},
        "ingress": {"name": "online-shop-frontend", "namespace": "online-shop-stage",
                    "class_name": "nginx", "paths": ["/stage"]},
    }


class RuntimeAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.certificates = CertificateFixture(self.directory)
        self.gateway_cert, self.gateway_key = self.certificates.issue(
            self.directory, "gateway", uris=(GATEWAY_URI_SAN,), usages=(ExtendedKeyUsageOID.CLIENT_AUTH,),
        )
        self.gateway_server_cert, self.gateway_server_key = self.certificates.issue(
            self.directory, "gateway-server", dns=(GATEWAY_DNS_SAN,), usages=(ExtendedKeyUsageOID.SERVER_AUTH,),
        )
        self.source_cert, self.source_key = self.certificates.issue(
            self.directory, "source", dns=(SOURCE_DNS_SAN,), uris=(SOURCE_URI_SAN,),
            usages=(ExtendedKeyUsageOID.SERVER_AUTH,),
        )
        self.api_peer = peer(dns=("kubernetes.default.svc",))
        self.source_peer = certificate_from_file(str(self.source_cert))
        self.gateway_peer = certificate_from_file(str(self.gateway_cert))
        self.network = patch("socket.create_connection", side_effect=AssertionError("network forbidden")).start()

    def tearDown(self) -> None:
        patch.stopall()
        self.temp.cleanup()

    def config(self, role: str) -> RuntimeConfig:
        cert, key = ((self.gateway_cert, self.gateway_key) if role == GATEWAY_ROLE else (self.source_cert, self.source_key))
        return RuntimeConfig(
            role=role, listen_address="127.0.0.1", listen_port=8443,
            internal_ca_file=str(self.certificates.ca_file),
            kubernetes_ca_file=str(self.certificates.kubernetes_ca_file),
            certificate_file=str(cert), private_key_file=str(key),
            projected_token_file=str(self.certificates.token_file),
            gateway_server_certificate_file=str(self.gateway_server_cert) if role == GATEWAY_ROLE else None,
            gateway_server_private_key_file=str(self.gateway_server_key) if role == GATEWAY_ROLE else None,
        )

    def source_executor(self, overrides: dict[str, bytes] | None = None) -> Executor:
        bodies = {ROLLOUT_PATH: encoded(rollout()), INGRESS_PATH: encoded(ingress()), APPLICATION_PATH: encoded(application())}
        bodies.update(overrides or {})
        return Executor({"kubernetes.default.svc": self.api_peer},
                        lambda host, request: Response(bodies[request["path"]]))

    def gateway_executor(self) -> Executor:
        def respond(host, request):
            if host == "kubernetes.default.svc":
                return Response(encoded({
                    "apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
                    "status": {"authenticated": True, "audiences": [EXTERNAL_TOKEN_AUDIENCE],
                               "user": {"username": VALIDATION_KUBERNETES_SUBJECT}},
                }), status=201)
            body = projected_state() if request["path"] == SOURCE_STATE_PATH else {
                "application": "online-shop-stage", "revision": "b" * 40,
                "sync_status": "Synced", "health_status": "Healthy",
            }
            return Response(encoded(body))

        return Executor({"kubernetes.default.svc": self.api_peer, SOURCE_DNS_SAN: self.source_peer}, respond)

    def gateway_request(self, kinds: list[str]) -> dict:
        end = datetime.now(UTC) - timedelta(seconds=1)
        return {
            "schema_version": SCHEMA_VERSION, "operation": "collect_staging_frontend_evidence",
            "request_id": "runtime-assembly-test",
            "time_range": {"start": (end - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                           "end": end.isoformat().replace("+00:00", "Z")},
            "evidence_kinds": kinds,
        }

    def test_config_is_typed_bounded_and_fails_closed(self) -> None:
        self.assertEqual(
            {item.name for item in fields(RuntimeConfig)},
            {"role", "listen_address", "listen_port", "internal_ca_file", "kubernetes_ca_file",
             "certificate_file", "private_key_file", "projected_token_file",
             "gateway_server_certificate_file", "gateway_server_private_key_file"},
        )
        self.config(GATEWAY_ROLE).validate()
        for changed in (
            {"role": "other"}, {"listen_address": "public.example"}, {"listen_port": 0},
            {"projected_token_file": "relative-token"}, {"certificate_file": str(self.directory / "missing")},
        ):
            with self.subTest(changed=changed), self.assertRaises(RuntimeAssemblyError):
                replace(self.config(GATEWAY_ROLE), **changed).validate()
        with self.assertRaises(RuntimeAssemblyError):
            replace(self.config(SOURCE_ROLE), listen_port=9443).validate()
        with self.assertRaises(RuntimeAssemblyError):
            replace(self.config(GATEWAY_ROLE), gateway_server_private_key_file=None).validate()
        with self.assertRaises(RuntimeAssemblyError):
            replace(self.config(SOURCE_ROLE), gateway_server_certificate_file=str(self.gateway_server_cert),
                    gateway_server_private_key_file=str(self.gateway_server_key)).validate()
        with self.assertRaises(RuntimeAssemblyError):
            replace(self.config(GATEWAY_ROLE), gateway_server_certificate_file=str(self.gateway_cert),
                    gateway_server_private_key_file=str(self.gateway_key)).validate()

        corrupted = self.directory / "corrupted.db"
        corrupted.write_bytes(b"not sqlite")
        with self.assertRaises(RuntimeAssemblyError):
            build_runtime(self.config(GATEWAY_ROLE), executor=self.gateway_executor(), state_path=str(corrupted))

    def test_gateway_collects_only_state_and_revision_through_fixed_source_routes(self) -> None:
        executor = self.gateway_executor()
        runtime = build_runtime(self.config(GATEWAY_ROLE), executor=executor,
                                state_path=str(self.directory / "runtime.db"))
        try:
            nonce = base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")
            response = runtime.application.handle(
                method="POST", path=HTTP_OPERATION_PATH,
                headers={"Authorization": "Bearer caller", "Content-Type": "application/json",
                         "X-Request-Nonce": nonce},
                body=encoded(self.gateway_request(["kubernetes_state", "deployment_revision"])),
            )
            self.assertEqual(response.status, 200)
            source_paths = [item["path"] for item in executor.requests if item["host"] == SOURCE_DNS_SAN]
            self.assertEqual(source_paths, [SOURCE_STATE_PATH, SOURCE_REVISION_PATH])
            self.assertEqual(response.body["result"]["section_count"], 2)
        finally:
            runtime.close()

    def test_unavailable_capabilities_and_generic_inputs_make_zero_source_calls(self) -> None:
        for index, mutation in enumerate((
            {"evidence_kinds": ["logs"]}, {"evidence_kinds": ["gitops"]},
            {"evidence_kinds": ["kubernetes_pod_status"]}, {"evidence_kinds": ["kubernetes_events"]},
            {"evidence_kinds": ["prometheus"]}, {"selector": "*"}, {"url": "https://example.invalid"},
            {"query": "up"}, {"ref": "main"},
        )):
            executor = self.gateway_executor()
            runtime = build_runtime(self.config(GATEWAY_ROLE), executor=executor,
                                    state_path=str(self.directory / f"denied-{index}.db"))
            try:
                request = {**self.gateway_request(["kubernetes_state"]), **mutation,
                           "request_id": f"denied-{index}"}
                nonce = base64.urlsafe_b64encode(bytes([index + 1]) * 32).decode().rstrip("=")
                response = runtime.application.handle(
                    method="POST", path=HTTP_OPERATION_PATH,
                    headers={"Authorization": "Bearer caller", "Content-Type": "application/json",
                             "X-Request-Nonce": nonce}, body=encoded(request),
                )
                self.assertEqual(response.status, 400)
                self.assertEqual([item for item in executor.requests if item["host"] == SOURCE_DNS_SAN], [])
            finally:
                runtime.close()

    def test_source_uses_only_exact_kubernetes_and_argocd_paths_and_projects_output(self) -> None:
        executor = self.source_executor()
        runtime = build_runtime(self.config(SOURCE_ROLE), executor=executor)
        headers = {"Host": SOURCE_DNS_SAN, "Accept": "application/json", "Accept-Encoding": "identity",
                   "Content-Length": "0"}
        state = runtime.application.handle(method="GET", path=SOURCE_STATE_PATH, headers=headers, body=b"",
                                           peer=self.gateway_peer)
        revision = runtime.application.handle(method="GET", path=SOURCE_REVISION_PATH, headers=headers, body=b"",
                                              peer=self.gateway_peer)
        self.assertEqual(state.status, 200)
        self.assertEqual(state.body, projected_state())
        self.assertNotIn("uid", str(state.body))
        self.assertEqual(revision.status, 200)
        self.assertEqual([item["path"] for item in executor.requests], [ROLLOUT_PATH, INGRESS_PATH, APPLICATION_PATH])
        self.assertTrue(all(item["method"] == "GET" and item["body"] == b"" for item in executor.requests))
        self.assertTrue(all(item["headers"]["Authorization"] == "Bearer fixture-runtime-token" for item in executor.requests))

    def test_rejected_source_transport_identity_and_plaintext_make_zero_backend_calls(self) -> None:
        for bad_peer in (
            replace(self.gateway_peer, trusted_by_configured_ca=False),
            replace(self.gateway_peer, uri_sans=frozenset({"spiffe://wrong"})),
            replace(self.gateway_peer, extended_key_usages=frozenset({"serverAuth"})),
            replace(self.gateway_peer, not_after=datetime.now(UTC) - timedelta(microseconds=1)),
        ):
            executor = self.source_executor()
            runtime = build_runtime(self.config(SOURCE_ROLE), executor=executor)
            response = runtime.application.handle(
                method="GET", path=SOURCE_STATE_PATH,
                headers={"Host": SOURCE_DNS_SAN}, body=b"", peer=bad_peer,
            )
            self.assertEqual(response.status, 401)
            self.assertEqual(executor.requests, [])
        with self.assertRaises(RuntimeAssemblyError):
            _source_peer(object())

    def test_source_startup_rejects_wrong_san_eku_expiry_and_tls_files(self) -> None:
        wrong_certificates = [
            self.certificates.issue(self.directory, "wrong-san", dns=("wrong",), uris=(SOURCE_URI_SAN,),
                                    usages=(ExtendedKeyUsageOID.SERVER_AUTH,)),
            self.certificates.issue(self.directory, "wrong-eku", dns=(SOURCE_DNS_SAN,), uris=(SOURCE_URI_SAN,),
                                    usages=(ExtendedKeyUsageOID.CLIENT_AUTH,)),
            self.certificates.issue(self.directory, "expired", dns=(SOURCE_DNS_SAN,), uris=(SOURCE_URI_SAN,),
                                    usages=(ExtendedKeyUsageOID.SERVER_AUTH,),
                                    not_before=self.certificates.now - timedelta(days=2),
                                    not_after=self.certificates.now - timedelta(days=1)),
        ]
        for cert, key in wrong_certificates:
            with self.assertRaises(ProviderError):
                build_runtime(replace(self.config(SOURCE_ROLE), certificate_file=str(cert), private_key_file=str(key)),
                              executor=self.source_executor(), now=self.certificates.now)
        bad_ca = self.directory / "bad-ca.pem"
        bad_ca.write_text("not a CA", encoding="ascii")
        with self.assertRaises(ProviderError):
            build_runtime(replace(self.config(SOURCE_ROLE), internal_ca_file=str(bad_ca)),
                          executor=self.source_executor())

    def test_gateway_startup_rejects_wrong_client_identity_and_projected_token(self) -> None:
        wrong_certificates = [
            self.certificates.issue(self.directory, "gateway-wrong-san", uris=("spiffe://wrong",),
                                    usages=(ExtendedKeyUsageOID.CLIENT_AUTH,)),
            self.certificates.issue(self.directory, "gateway-wrong-eku", uris=(GATEWAY_URI_SAN,),
                                    usages=(ExtendedKeyUsageOID.SERVER_AUTH,)),
            self.certificates.issue(self.directory, "gateway-expired", uris=(GATEWAY_URI_SAN,),
                                    usages=(ExtendedKeyUsageOID.CLIENT_AUTH,),
                                    not_before=self.certificates.now - timedelta(days=2),
                                    not_after=self.certificates.now - timedelta(days=1)),
        ]
        for index, (cert, key) in enumerate(wrong_certificates):
            executor = self.gateway_executor()
            with self.assertRaises(ProviderError):
                build_runtime(
                    replace(self.config(GATEWAY_ROLE), certificate_file=str(cert), private_key_file=str(key)),
                    executor=executor, state_path=str(self.directory / f"wrong-client-{index}.db"),
                    now=self.certificates.now,
                )
            self.assertEqual(executor.opens, [])
        self.certificates.token_file.write_text("bad token with spaces", encoding="ascii")
        with self.assertRaises(ProviderError):
            build_runtime(self.config(GATEWAY_ROLE), executor=self.gateway_executor(),
                          state_path=str(self.directory / "bad-token.db"))

    def test_gateway_server_tls_identity_fails_before_listener_creation(self) -> None:
        wrong_certificates = [
            self.certificates.issue(self.directory, "gateway-server-wrong-san", dns=("wrong",),
                                    usages=(ExtendedKeyUsageOID.SERVER_AUTH,)),
            self.certificates.issue(self.directory, "gateway-server-wrong-eku", dns=(GATEWAY_DNS_SAN,),
                                    usages=(ExtendedKeyUsageOID.CLIENT_AUTH,)),
            self.certificates.issue(self.directory, "gateway-server-expired", dns=(GATEWAY_DNS_SAN,),
                                    usages=(ExtendedKeyUsageOID.SERVER_AUTH,),
                                    not_before=self.certificates.now - timedelta(days=2),
                                    not_after=self.certificates.now - timedelta(days=1)),
        ]
        malformed = self.directory / "gateway-server-malformed.pem"
        malformed.write_text("not a certificate", encoding="ascii")
        wrong_certificates.append((malformed, self.gateway_server_key))
        bad_ca = self.directory / "gateway-server-bad-ca.pem"
        bad_ca.write_text("not a CA", encoding="ascii")
        configs = [
            replace(self.config(GATEWAY_ROLE), gateway_server_certificate_file=str(cert),
                    gateway_server_private_key_file=str(key))
            for cert, key in wrong_certificates
        ]
        configs.extend((
            replace(self.config(GATEWAY_ROLE), gateway_server_private_key_file=str(self.directory / "missing.key")),
            replace(self.config(GATEWAY_ROLE), internal_ca_file=str(self.directory / "missing-ca.pem")),
            replace(self.config(GATEWAY_ROLE), internal_ca_file=str(bad_ca)),
        ))
        for index, config in enumerate(configs):
            with self.subTest(index=index), \
                 patch("evidence_gateway.runtime.assembly.ThreadingHTTPServer") as listener, \
                 self.assertRaises((ProviderError, RuntimeAssemblyError)):
                serve(config)
            listener.assert_not_called()

    def test_gateway_server_tls_is_not_client_mtls_and_source_stays_mtls_required(self) -> None:
        gateway = build_runtime(self.config(GATEWAY_ROLE), executor=self.gateway_executor(),
                                state_path=str(self.directory / "gateway-tls.db"))
        source = build_runtime(self.config(SOURCE_ROLE), executor=self.source_executor())
        try:
            self.assertEqual(gateway.server_context.verify_mode, ssl.CERT_NONE)
            self.assertEqual(source.server_context.verify_mode, ssl.CERT_REQUIRED)
            gateway_server_identity = certificate_from_file(str(self.gateway_server_cert))
            gateway_client_identity = certificate_from_file(str(self.gateway_cert))
            self.assertEqual(gateway_server_identity.dns_names, frozenset({GATEWAY_DNS_SAN}))
            self.assertEqual(gateway_server_identity.extended_key_usages, frozenset({"serverAuth"}))
            self.assertEqual(gateway_client_identity.uri_sans, frozenset({GATEWAY_URI_SAN}))
            self.assertEqual(gateway_client_identity.extended_key_usages, frozenset({"clientAuth"}))
        finally:
            gateway.close()
            source.close()

    def test_malformed_and_oversized_backend_responses_are_safe(self) -> None:
        malformed = rollout()
        malformed["metadata"]["name"] = "cart"
        cases = ({ROLLOUT_PATH: encoded(malformed)}, {ROLLOUT_PATH: b"{"},
                 {ROLLOUT_PATH: b" " * (64 * 1024 + 1)})
        for overrides in cases:
            executor = self.source_executor(overrides)
            runtime = build_runtime(self.config(SOURCE_ROLE), executor=executor)
            response = runtime.application.handle(
                method="GET", path=SOURCE_STATE_PATH,
                headers={"Host": SOURCE_DNS_SAN}, body=b"", peer=self.gateway_peer,
            )
            self.assertEqual(response.status, 503)
            self.assertEqual(response.body, {"outcome": "denied", "error": {"code": "backend_unavailable"}})
            self.assertNotIn("cart", str(response.body))

    def test_source_http_client_contract_remains_compatible(self) -> None:
        source_executor = self.source_executor()
        source_runtime = build_runtime(self.config(SOURCE_ROLE), executor=source_executor)

        def bridge(host, request):
            response = source_runtime.application.handle(
                method=request["method"], path=request["path"], headers=request["headers"],
                body=request["body"], peer=self.gateway_peer,
            )
            return Response(response.json_bytes(), response.status)

        gateway_executor = Executor({SOURCE_DNS_SAN: self.source_peer}, bridge)
        from evidence_gateway.runtime.http_clients import SourceHTTPClient, TLSConfiguration
        client = SourceHTTPClient(
            executor=gateway_executor,
            tls=TLSConfiguration(str(self.certificates.ca_file), str(self.gateway_cert), str(self.gateway_key),
                                 self.gateway_peer),
        )
        self.assertEqual(client.get_frontend_state(), projected_state())
        self.assertEqual(client.get_deployment_revision()["revision"], "b" * 40)
        self.assertEqual([item["path"] for item in gateway_executor.requests],
                         [SOURCE_STATE_PATH, SOURCE_REVISION_PATH])

    def test_image_is_single_role_selected_digest_pinned_package(self) -> None:
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("python:3.13.7-slim-bookworm@sha256:", dockerfile)
        self.assertIn('ENTRYPOINT ["python", "-m", "evidence_gateway.runtime"]', dockerfile)
        self.assertNotIn("gcr.io", dockerfile)
        self.assertNotIn("pkg.dev", dockerfile)
        self.assertNotIn("COPY environments", dockerfile)
        self.assertTrue(dockerignore.startswith("**\n"))
        self.assertIn("!evidence_gateway/**", dockerignore)
        self.assertNotIn("!docs", dockerignore)
        self.assertEqual(self.network.call_count, 0)

    def test_role_entrypoint_wraps_every_listener_in_tls(self) -> None:
        class Context:
            def __init__(self):
                self.calls = []

            def wrap_socket(self, socket, *, server_side):
                self.calls.append((socket, server_side))
                return "tls-socket"

        class Server:
            instances = []

            def __init__(self, address, handler):
                self.address = address
                self.handler = handler
                self.socket = object()
                self.served = False
                self.closed = False
                Server.instances.append(self)

            def serve_forever(self):
                self.served = True

            def server_close(self):
                self.closed = True

        for role in (GATEWAY_ROLE, SOURCE_ROLE):
            context = Context()
            built = BuiltRuntime(role, object(), context)
            with patch("evidence_gateway.runtime.assembly.build_runtime", return_value=built), \
                 patch("evidence_gateway.runtime.assembly.ThreadingHTTPServer", Server):
                serve(self.config(role))
            server = Server.instances[-1]
            self.assertTrue(server.served and server.closed)
            self.assertEqual(server.socket, "tls-socket")
            self.assertEqual(context.calls[0][1], True)
        self.assertEqual(self.network.call_count, 0)


if __name__ == "__main__":
    unittest.main()
