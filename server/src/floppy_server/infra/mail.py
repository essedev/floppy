"""
Outgoing mail: sign-in links and codes, invitations, the settings test.

`SmtpMailer` sends through an SMTP server, the organization's own or the instance's.
`ConsoleMailer` prints the mail instead, for development only (the settings refuse it in
production: it prints codes and links).
"""

import logging
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

import aiosmtplib

log = logging.getLogger(__name__)


class MailError(Exception):
    """The mail could not be handed to the SMTP server, or no server is configured."""


@dataclass(frozen=True)
class SmtpConfig:
    """An SMTP server to send through."""

    host: str
    port: int
    username: str | None
    password: str | None
    sender: str
    starttls: bool = True
    timeout_seconds: float = 15.0

    def __repr__(self) -> str:
        return f"SmtpConfig(host={self.host!r}, port={self.port}, username={self.username!r})"


@dataclass(frozen=True)
class Mail:
    """One plain-text message."""

    to: str
    subject: str
    text: str


class Mailer(Protocol):
    """Sends a message through an SMTP configuration (None: none is configured)."""

    async def send(self, mail: Mail, via: SmtpConfig | None) -> None:
        """Hands the message over, or raises `MailError`."""
        ...


class SmtpMailer:
    """`Mailer` over SMTP, with STARTTLS on submission ports and implicit TLS on 465."""

    async def send(self, mail: Mail, via: SmtpConfig | None) -> None:
        if via is None:
            raise MailError("no SMTP server is configured")
        message = EmailMessage()
        message["From"] = via.sender
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message["Message-ID"] = make_msgid(domain=via.sender.rsplit("@", 1)[-1].strip("> "))
        message.set_content(mail.text)
        implicit_tls = via.port == 465
        try:
            await aiosmtplib.send(
                message,
                hostname=via.host,
                port=via.port,
                username=via.username or None,
                password=via.password or None,
                use_tls=implicit_tls,
                start_tls=None if implicit_tls or not via.starttls else True,
                timeout=via.timeout_seconds,
            )
        except (aiosmtplib.SMTPException, OSError) as error:
            raise MailError(f"{type(error).__name__}: {error}") from error


class ConsoleMailer:
    """`Mailer` that prints messages to the log. Development only."""

    async def send(self, mail: Mail, via: SmtpConfig | None) -> None:
        log.warning(
            "console mail (development only)\nTo: %s\nSubject: %s\n\n%s",
            mail.to,
            mail.subject,
            mail.text,
        )
