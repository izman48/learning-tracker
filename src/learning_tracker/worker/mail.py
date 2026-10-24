"""Outgoing mail: SMTP with TLS required and verified, or no mailer at all.

- STARTTLS (port 587) or implicit TLS (port 465). There is no plaintext mode: a
  server that does not offer STARTTLS, or whose certificate does not verify, is
  refused before any login or message is sent.
- Every network step has a timeout.
- Messages are plain text, built with the standard library's EmailMessage, whose
  SMTP policy refuses line breaks in header values. The recipient must be one bare
  address, so no user input can add a header or a second recipient.
- Errors never carry the SMTP password or the message body.

Only the worker sends mail, so the SMTP password lives only in the worker.
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr
from typing import Literal, Protocol

from anyio import to_thread

from learning_tracker.config import Settings

Mode = Literal["starttls", "implicit"]
DEFAULT_TIMEOUT_S = 10.0


class MailError(Exception):
    """Sending failed. The message is safe to log."""


class MailerNotConfigured(MailError):
    """No mailer is set up (LT_SMTP_HOST is unset); callers must not send."""


class Mailer(Protocol):
    async def send(self, to: str, subject: str, text: str) -> None: ...


def _one_bare_address(to: str) -> str:
    name, address = parseaddr(to)
    if name or not address or address != to.strip() or "@" not in address:
        raise ValueError("the recipient must be one bare email address")
    if any(c in to for c in "\r\n,;<>"):
        raise ValueError("the recipient must be one bare email address")
    return address


def build_message(*, sender: str, to: str, subject: str, text: str) -> EmailMessage:
    message = EmailMessage(policy=SMTP)
    message["From"] = sender  # validated at startup (LT_SMTP_FROM)
    message["To"] = _one_bare_address(to)
    message["Subject"] = subject  # the policy refuses CR/LF in any header value
    message.set_content(text)
    return message


class SmtpMailer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        mode: Mode,
        user: str | None,
        password: str | None,
        sender: str,
        ssl_context: ssl.SSLContext | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._host, self._port, self._mode = host, port, mode
        self._user, self._password = user, password
        self._sender = sender
        # The default context verifies the certificate and the host name.
        self._tls = ssl_context or ssl.create_default_context()
        self._timeout = timeout_s

    def __repr__(self) -> str:
        return f"SmtpMailer(host={self._host!r}, port={self._port}, mode={self._mode!r})"

    async def send(self, to: str, subject: str, text: str) -> None:
        message = build_message(sender=self._sender, to=to, subject=subject, text=text)
        await to_thread.run_sync(self._send_blocking, message)

    def _send_blocking(self, message: EmailMessage) -> None:
        try:
            with self._connect() as smtp:
                if self._user and self._password:
                    smtp.login(self._user, self._password)
                smtp.send_message(message)
        except MailError:
            raise
        except smtplib.SMTPAuthenticationError:
            raise MailError("the SMTP server rejected the login") from None
        except ssl.SSLError:
            raise MailError("TLS failed: the SMTP server's certificate did not verify") from None
        except TimeoutError:
            raise MailError("the SMTP server timed out") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(
                f"the SMTP server is unreachable or refused ({type(exc).__name__})"
            ) from None

    def _connect(self) -> smtplib.SMTP:
        if self._mode == "implicit":
            return smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout, context=self._tls
            )
        smtp = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        try:
            smtp.ehlo()
            if not smtp.has_extn("starttls"):
                raise MailError("TLS required: the SMTP server does not offer STARTTLS")
            smtp.starttls(context=self._tls)
            smtp.ehlo()
        except BaseException:
            smtp.close()
            raise
        return smtp


class NullMailer:
    """Used when no mailer is configured. It refuses rather than pretending to send."""

    async def send(self, to: str, subject: str, text: str) -> None:
        raise MailerNotConfigured("no mailer is configured")


def mailer_from_settings(settings: Settings) -> Mailer:
    """Port 465 means implicit TLS; 587 means STARTTLS (the settings allow only these)."""
    if settings.smtp_host is None:
        return NullMailer()
    if settings.smtp_from is None:
        raise MailerNotConfigured("LT_SMTP_FROM is required when LT_SMTP_HOST is set")
    return SmtpMailer(
        host=settings.smtp_host,
        port=settings.smtp_port,
        mode="implicit" if settings.smtp_port == 465 else "starttls",
        user=settings.smtp_user,
        password=settings.smtp_password.reveal() if settings.smtp_password else None,
        sender=settings.smtp_from,
    )
