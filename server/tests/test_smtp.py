"""`SmtpMailer` against a real SMTP server in the test process (aiosmtpd), with authentication."""

import socket
from collections.abc import Iterator
from email import message_from_bytes, policy
from typing import Any

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from floppy_server.infra.mail import Mail, MailError, SmtpConfig, SmtpMailer


class Inbox:
    """Keeps every message the server accepts."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], bytes]] = []

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802 (aiosmtpd's hook name)
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content))
        return "250 OK"


def _authenticator(
    server: Any, session: Any, envelope: Any, mechanism: str, data: Any
) -> AuthResult:
    ok = isinstance(data, LoginPassword) and (data.login, data.password) == (
        b"floppy",
        b"smtp-secret",
    )
    return AuthResult(success=ok)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def smtp_server() -> Iterator[tuple[Inbox, int]]:
    inbox, port = Inbox(), _free_port()
    controller = Controller(
        inbox,
        hostname="127.0.0.1",
        port=port,
        authenticator=_authenticator,
        auth_require_tls=False,
    )
    controller.start()
    yield inbox, port
    controller.stop()


def config(port: int, password: str = "smtp-secret") -> SmtpConfig:
    return SmtpConfig(
        host="127.0.0.1",
        port=port,
        username="floppy",
        password=password,
        sender="Floppy <floppy@studio.test>",
        starttls=False,
        timeout_seconds=5,
    )


async def test_mail_is_delivered_with_authentication(smtp_server: tuple[Inbox, int]) -> None:
    inbox, port = smtp_server
    await SmtpMailer().send(
        Mail(to="giulia@studio.test", subject="Floppy · prova", text="Ciao, è una prova."),
        config(port),
    )
    assert len(inbox.messages) == 1
    sender, recipients, content = inbox.messages[0]
    assert (sender, recipients) == ("floppy@studio.test", ["giulia@studio.test"])
    message = message_from_bytes(content, policy=policy.default)
    assert message["Subject"] == "Floppy · prova"
    assert "è una prova" in message.get_content()


async def test_wrong_credentials_and_no_server_are_mail_errors(
    smtp_server: tuple[Inbox, int],
) -> None:
    _, port = smtp_server
    with pytest.raises(MailError):
        await SmtpMailer().send(
            Mail(to="a@b.test", subject="s", text="t"), config(port, password="wrong")
        )
    with pytest.raises(MailError):
        await SmtpMailer().send(Mail(to="a@b.test", subject="s", text="t"), config(_free_port()))
    with pytest.raises(MailError, match="no SMTP server"):
        await SmtpMailer().send(Mail(to="a@b.test", subject="s", text="t"), None)
