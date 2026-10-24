"""SMTP with TLS required and verified, a timeout, and no way to inject headers."""

import base64
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from email import message_from_bytes
from email.policy import default

import pytest

from learning_tracker.config import ConfigError, load_settings
from learning_tracker.worker.mail import (
    MailerNotConfigured,
    MailError,
    NullMailer,
    SmtpMailer,
    build_message,
    mailer_from_settings,
)
from tests.worker.mail.conftest import PASSWORD, USER, Server

pytestmark = pytest.mark.anyio
SENDER = "Reads <no-reply@reads.example.com>"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def mailer(port: int, mode: str, context: ssl.SSLContext | None, **kw: object) -> SmtpMailer:
    return SmtpMailer(
        host="127.0.0.1",
        port=port,
        mode=mode,  # type: ignore[arg-type]
        user=USER,
        password=PASSWORD,
        sender=SENDER,
        ssl_context=context,
        **kw,  # type: ignore[arg-type]
    )


async def test_starttls_delivers_plain_text_after_login(
    starttls_server: Server, client_tls: ssl.SSLContext
) -> None:
    await mailer(starttls_server.port, "starttls", client_tls).send(
        "reader@example.com", "Reset your learning-tracker password", "Open this link."
    )

    [raw] = starttls_server.inbox.messages
    message = message_from_bytes(raw, policy=default)
    assert message["From"] == SENDER
    assert message["To"] == "reader@example.com"
    assert message["Subject"] == "Reset your learning-tracker password"
    assert message.get_content_type() == "text/plain"
    assert message.get_content().strip() == "Open this link."
    assert starttls_server.inbox.recipients == [["reader@example.com"]]
    assert starttls_server.inbox.logins == [USER]


async def test_implicit_tls_delivers(
    implicit_tls_server: Server, client_tls: ssl.SSLContext
) -> None:
    await mailer(implicit_tls_server.port, "implicit", client_tls).send(
        "reader@example.com", "Subject", "Body"
    )

    assert len(implicit_tls_server.inbox.messages) == 1


async def test_a_server_without_starttls_is_refused_and_nothing_is_sent(
    plaintext_server: Server, client_tls: ssl.SSLContext
) -> None:
    with pytest.raises(MailError, match="TLS"):
        await mailer(plaintext_server.port, "starttls", client_tls).send(
            "reader@example.com", "Subject", "Body"
        )

    assert plaintext_server.inbox.messages == []


async def test_an_unverifiable_certificate_is_refused(starttls_server: Server) -> None:
    # The default context, as in production: the test CA is not trusted.
    with pytest.raises(MailError, match="TLS"):
        await mailer(starttls_server.port, "starttls", None).send(
            "reader@example.com", "Subject", "Body"
        )

    assert starttls_server.inbox.messages == []


async def test_a_wrong_password_fails_without_revealing_it(
    starttls_server: Server, client_tls: ssl.SSLContext
) -> None:
    bad = SmtpMailer(
        host="127.0.0.1",
        port=starttls_server.port,
        mode="starttls",
        user=USER,
        password="not-the-password",
        sender=SENDER,
        ssl_context=client_tls,
    )

    with pytest.raises(MailError) as failure:
        await bad.send("reader@example.com", "Subject", "Body")

    assert "not-the-password" not in str(failure.value)
    assert starttls_server.inbox.messages == []


@pytest.fixture
def silent_server() -> Iterator[int]:
    """Accepts connections and never says a word."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(listener.accept()[0])
            except TimeoutError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield int(listener.getsockname()[1])
    stop.set()
    thread.join()
    for sock in held:
        sock.close()
    listener.close()


async def test_a_silent_server_times_out(silent_server: int, client_tls: ssl.SSLContext) -> None:
    started = time.monotonic()
    with pytest.raises(MailError, match=r"timed out|unreachable"):
        await mailer(silent_server, "starttls", client_tls, timeout_s=1.0).send(
            "reader@example.com", "Subject", "Body"
        )

    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "to",
    [
        "reader@example.com\r\nBcc: victim@example.org",
        "reader@example.com\nBcc: victim@example.org",
        "reader@example.com, other@example.org",
        "Reader <reader@example.com>",
        "not-an-address",
        "",
    ],
)
def test_recipients_are_one_bare_address(to: str) -> None:
    with pytest.raises(ValueError):
        build_message(sender=SENDER, to=to, subject="Subject", text="Body")


@pytest.mark.parametrize("subject", ["Hi\r\nBcc: victim@example.org", "Hi\nX-Evil: 1"])
def test_header_values_cannot_carry_line_breaks(subject: str) -> None:
    with pytest.raises(ValueError):
        build_message(sender=SENDER, to="reader@example.com", subject=subject, text="Body")


def test_a_clean_message_is_built() -> None:
    message = build_message(
        sender=SENDER, to="reader@example.com", subject="Subject", text="Line one.\nLine two."
    )

    assert message.get_content_type() == "text/plain"
    assert "Bcc" not in message and "Cc" not in message
    assert message["To"] == "reader@example.com"


async def test_the_null_mailer_refuses_to_pretend() -> None:
    with pytest.raises(MailerNotConfigured):
        await NullMailer().send("reader@example.com", "Subject", "Body")


WORKER_ENV = {
    "LT_SECRET_KEY": base64.b64encode(bytes(range(48))).decode(),
    "LT_DB_PASSWORD": "pw-for-tests",
    "LT_PUBLIC_ORIGIN": "https://reads.example.com",
    "LT_ANTHROPIC_API_KEY": "key-for-tests",
}


def test_no_smtp_host_means_the_null_mailer() -> None:
    assert isinstance(mailer_from_settings(load_settings("worker", WORKER_ENV)), NullMailer)


@pytest.mark.parametrize(("port", "mode"), [("587", "starttls"), ("465", "implicit")])
def test_the_port_picks_the_tls_mode(port: str, mode: str) -> None:
    settings = load_settings(
        "worker",
        {
            **WORKER_ENV,
            "LT_SMTP_HOST": "smtp.example.com",
            "LT_SMTP_PORT": port,
            "LT_SMTP_USER": "sender",
            "LT_SMTP_PASSWORD": "smtp-pass-for-tests",
            "LT_SMTP_FROM": SENDER,
        },
    )

    built = mailer_from_settings(settings)

    assert isinstance(built, SmtpMailer)
    assert f"mode={mode!r}" in repr(built) and "smtp-pass-for-tests" not in repr(built)


def test_web_is_never_given_the_smtp_password() -> None:
    web_env = {k: v for k, v in WORKER_ENV.items() if k != "LT_ANTHROPIC_API_KEY"}
    with pytest.raises(ConfigError, match="LT_SMTP_PASSWORD must not be given to web"):
        load_settings("web", {**web_env, "LT_SMTP_PASSWORD": "smtp-pass-for-tests"})
