"""A local SMTP server (aiosmtpd) with a throwaway CA, for mailer tests.

Nothing leaves the machine. The certificate is made per test session for
127.0.0.1, and only the test's own SSL context trusts it, so the default
context (as in production) refuses it.
"""

import datetime
import ipaddress
import socket
import ssl
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

USER, PASSWORD = "mailer-user", "mailer-pass-for-tests"


@dataclass
class Inbox:
    messages: list[bytes] = field(default_factory=list)
    recipients: list[list[str]] = field(default_factory=list)
    logins: list[str] = field(default_factory=list)

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:
        self.messages.append(envelope.content)
        self.recipients.append(list(envelope.rcpt_tos))
        return "250 OK"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def tls_files(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    cert_file, key_file = folder / "cert.pem", folder / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture
def client_tls(tls_files: tuple[Path, Path]) -> ssl.SSLContext:
    """A verifying client context that trusts only the test CA."""
    context = ssl.create_default_context(cafile=str(tls_files[0]))
    return context


def _server_tls(tls_files: tuple[Path, Path]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(tls_files[0]), str(tls_files[1]))
    return context


def _authenticator(inbox: Inbox) -> Any:
    def check(server: Any, session: Any, envelope: Any, mechanism: str, data: Any) -> AuthResult:
        ok = isinstance(data, LoginPassword) and (data.login, data.password) == (
            USER.encode(),
            PASSWORD.encode(),
        )
        if ok:
            inbox.logins.append(USER)
        return AuthResult(success=ok)

    return check


@dataclass
class Server:
    port: int
    inbox: Inbox


def _run(controller: Controller, inbox: Inbox, port: int) -> Iterator[Server]:
    controller.start()
    try:
        yield Server(port, inbox)
    finally:
        controller.stop()


@pytest.fixture
def starttls_server(tls_files: tuple[Path, Path]) -> Iterator[Server]:
    inbox, port = Inbox(), free_port()
    controller = Controller(
        inbox,
        hostname="127.0.0.1",
        port=port,
        tls_context=_server_tls(tls_files),
        require_starttls=True,
        authenticator=_authenticator(inbox),
        auth_require_tls=True,
    )
    yield from _run(controller, inbox, port)


@pytest.fixture
def implicit_tls_server(tls_files: tuple[Path, Path]) -> Iterator[Server]:
    inbox, port = Inbox(), free_port()
    controller = Controller(
        inbox,
        hostname="127.0.0.1",
        port=port,
        ssl_context=_server_tls(tls_files),
        authenticator=_authenticator(inbox),
        # aiosmtpd doesn't count implicit TLS as TLS for AUTH; the socket is TLS.
        auth_require_tls=False,
    )
    yield from _run(controller, inbox, port)


@pytest.fixture
def plaintext_server() -> Iterator[Server]:
    inbox, port = Inbox(), free_port()
    controller = Controller(inbox, hostname="127.0.0.1", port=port)
    yield from _run(controller, inbox, port)
