"""Concrete TLS transport primitives for the repository runtime assembly."""

from __future__ import annotations

import http.client
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from ..providers import ProviderError
from .http_clients import HTTPExecutor, HTTPResponse, TLSConnection
from .transports import InternalMTLSValidator, PeerCertificate, SOURCE_DNS_SAN, SOURCE_URI_SAN

MAX_CERTIFICATE_BYTES = 64 * 1024
MAX_RESPONSE_HEADERS = 16
MAX_RESPONSE_HEADER_BYTES = 8 * 1024
_SAFE_ERROR = "approved HTTPS transport is unavailable"


def certificate_from_file(path: str, *, trusted: bool = True) -> PeerCertificate:
    try:
        data = _read_bounded(path, MAX_CERTIFICATE_BYTES)
        certificate = x509.load_pem_x509_certificate(data)
        return _certificate_facts(certificate, trusted=trusted)
    except Exception:
        raise ProviderError("internal certificate is unavailable") from None


def certificate_from_der(data: bytes, *, trusted: bool = True) -> PeerCertificate:
    try:
        if not isinstance(data, bytes) or not data or len(data) > MAX_CERTIFICATE_BYTES:
            raise ValueError()
        return _certificate_facts(x509.load_der_x509_certificate(data), trusted=trusted)
    except Exception:
        raise ProviderError("internal certificate is unavailable") from None


def build_source_server_context(
    *, ca_file: str, certificate_file: str, private_key_file: str,
    now: datetime | None = None, context_factory: Any = ssl.SSLContext,
) -> ssl.SSLContext:
    try:
        current = now or datetime.now(UTC)
        identity = certificate_from_file(certificate_file)
        InternalMTLSValidator().validate_server(
            identity, expected_dns=SOURCE_DNS_SAN, expected_uri=SOURCE_URI_SAN, now=current,
        )
        context = context_factory(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cafile=ca_file)
        context.load_cert_chain(certfile=certificate_file, keyfile=private_key_file)
        if context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError()
        return context
    except Exception:
        raise ProviderError("internal TLS listener is unavailable") from None


class StdlibHTTPExecutor(HTTPExecutor):
    """Direct TLS only: no proxy discovery, redirects, retries or plaintext path."""

    def open_tls(
        self, *, host: str, port: int, server_hostname: str, context: ssl.SSLContext, timeout: float,
    ) -> TLSConnection:
        connection: http.client.HTTPSConnection | None = None
        try:
            if (
                not isinstance(host, str) or not host or server_hostname != host
                or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(context, ssl.SSLContext) or context.verify_mode != ssl.CERT_REQUIRED
                or context.check_hostname is not True or not isinstance(timeout, (int, float)) or timeout <= 0
            ):
                raise ValueError()
            connection = http.client.HTTPSConnection(host=host, port=port, timeout=timeout, context=context)
            connection.connect()
            if connection.sock is None or not isinstance(connection.sock, ssl.SSLSocket):
                raise ssl.SSLError()
            return _StdlibTLSConnection(connection)
        except Exception:
            if connection is not None:
                connection.close()
            raise ProviderError(_SAFE_ERROR) from None


@dataclass
class _StdlibHTTPResponse(HTTPResponse):
    response: http.client.HTTPResponse
    socket: ssl.SSLSocket
    status: int
    headers: Mapping[str, str]

    def read(self, size: int, *, timeout: float) -> bytes:
        if type(size) is not int or size < 1 or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ProviderError(_SAFE_ERROR)
        if self.response.isclosed():
            return b""
        self.socket.settimeout(timeout)
        return self.response.read(size)


class _StdlibTLSConnection(TLSConnection):
    def __init__(self, connection: http.client.HTTPSConnection) -> None:
        self._connection = connection

    def peer_certificate(self) -> PeerCertificate:
        try:
            socket = self._socket()
            return certificate_from_der(socket.getpeercert(binary_form=True), trusted=True)
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None

    def request(
        self, *, method: str, path: str, headers: Mapping[str, str], body: bytes, timeout: float,
    ) -> HTTPResponse:
        try:
            if (
                method not in {"GET", "POST"} or not isinstance(path, str) or not path.startswith("/")
                or "\r" in path or "\n" in path or not isinstance(body, bytes)
                or not isinstance(headers, Mapping) or len(headers) > 16
            ):
                raise ValueError()
            normalized: dict[str, str] = {}
            for name, value in headers.items():
                if (
                    not isinstance(name, str) or not isinstance(value, str)
                    or not name or len(name) > 64 or len(value) > 8192
                    or any(character in name + value for character in "\r\n")
                    or name.lower() in normalized
                ):
                    raise ValueError()
                normalized[name.lower()] = value
            socket = self._socket()
            socket.settimeout(timeout)
            self._connection.request(method=method, url=path, body=body, headers=dict(headers))
            response = self._connection.getresponse()
            raw_headers = response.getheaders()
            if len(raw_headers) > MAX_RESPONSE_HEADERS:
                raise ValueError()
            projected: dict[str, str] = {}
            total = 0
            for name, value in raw_headers:
                total += len(name) + len(value) + 4
                key = name.lower()
                if total > MAX_RESPONSE_HEADER_BYTES or key in projected:
                    raise ValueError()
                projected[key] = value
            return _StdlibHTTPResponse(response, socket, response.status, projected)
        except Exception:
            raise ProviderError(_SAFE_ERROR) from None

    def close(self) -> None:
        self._connection.close()

    def _socket(self) -> ssl.SSLSocket:
        socket = self._connection.sock
        if socket is None or not isinstance(socket, ssl.SSLSocket):
            raise ProviderError(_SAFE_ERROR)
        return socket


def _certificate_facts(certificate: x509.Certificate, *, trusted: bool) -> PeerCertificate:
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    allowed_general_names = (x509.DNSName, x509.UniformResourceIdentifier)
    if any(not isinstance(name, allowed_general_names) for name in san):
        raise ValueError()
    eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    usages = []
    for usage in eku:
        if usage == ExtendedKeyUsageOID.CLIENT_AUTH:
            usages.append("clientAuth")
        elif usage == ExtendedKeyUsageOID.SERVER_AUTH:
            usages.append("serverAuth")
        else:
            usages.append(usage.dotted_string)
    return PeerCertificate(
        trusted_by_configured_ca=trusted,
        dns_names=frozenset(san.get_values_for_type(x509.DNSName)),
        uri_sans=frozenset(san.get_values_for_type(x509.UniformResourceIdentifier)),
        extended_key_usages=frozenset(usages),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
    )


def _read_bounded(path: str, limit: int) -> bytes:
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        raise ValueError()
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError()
    return data
