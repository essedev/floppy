"""
Test helpers: a recording mailer (a labelled fake of the SMTP server), reading links and
codes out of mail, and full flows through the API (signing in, enrolling a Mac).
"""

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from floppy_server.context import Context
from floppy_server.infra.mail import Mail, MailError, SmtpConfig


@dataclass
class RecordingMailer:
    """Fake mailer: keeps every message; `failing` refuses like a dead SMTP server."""

    outbox: list[Mail] = field(default_factory=list)
    failing: bool = False

    async def send(self, mail: Mail, via: SmtpConfig | None) -> None:
        if self.failing:
            raise MailError("SMTPConnectError: connection refused")
        self.outbox.append(mail)

    def last_to(self, address: str) -> Mail:
        """The latest message sent to an address."""
        for mail in reversed(self.outbox):
            if mail.to == address:
                return mail
        raise LookupError(f"no mail to {address}")


def mailer(ctx: Context) -> RecordingMailer:
    """The test context's mailer."""
    assert isinstance(ctx.mailer, RecordingMailer)
    return ctx.mailer


def token_in(mail: Mail) -> str:
    """The token of the link in a mail."""
    match = re.search(r"https?://\S+\?token=(\S+)", mail.text)
    assert match, mail.text
    return match.group(1)


def code_in(mail: Mail) -> str:
    """The 6-digit code in a mail."""
    match = re.search(r"\b(\d{6})\b", mail.text)
    assert match, mail.text
    return match.group(1)


async def sign_in(client: httpx.AsyncClient, ctx: Context, email: str) -> dict[str, Any]:
    """Signs a client in by magic link; returns the session."""
    response = await client.post("/auth/sign-in", json={"email": email})
    assert response.status_code == 200, response.text
    token = token_in(mailer(ctx).last_to(email.lower()))
    response = await client.post("/auth/magic-link", json={"token": token})
    assert response.status_code == 200, response.text
    session: dict[str, Any] = response.json()
    return session


async def invite_and_join(
    admin: httpx.AsyncClient,
    member: httpx.AsyncClient,
    ctx: Context,
    email: str,
    role: str = "member",
) -> dict[str, Any]:
    """Invites an address and accepts from the invitation link; returns the person."""
    response = await admin.post("/people", json={"email": email, "role": role})
    assert response.status_code == 201, response.text
    token = token_in(mailer(ctx).last_to(email))
    response = await member.post("/auth/invitation", json={"token": token})
    assert response.status_code == 200, response.text
    people: list[dict[str, Any]] = (await admin.get("/people")).json()
    return next(p for p in people if p["email"] == email)


def pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


@dataclass(frozen=True)
class EnrolledMac:
    """A Mac signed in from the app: its id, device token and, once registered, Valkey password."""

    id: str
    token: str
    valkey_user: str
    valkey_password: str | None
    s3: dict[str, str] | None = None

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def enrol_mac(
    app_client: httpx.AsyncClient,
    browser: httpx.AsyncClient,
    ctx: Context,
    email: str,
    *,
    name: str = "MacBook Pro di Giulia",
    register: bool = True,
    via_code: bool = False,
) -> EnrolledMac:
    """The app's sign-in end to end: request, link (or code), token exchange, registration."""
    verifier, challenge = pkce()
    response = await app_client.post(
        "/auth/app/sign-in",
        json={
            "email": email,
            "codeChallenge": challenge,
            "redirectUri": "http://127.0.0.1:53682/callback",
            "deviceName": name,
        },
    )
    assert response.status_code == 200, response.text
    mail = mailer(ctx).last_to(email)
    if via_code:
        body = {"codeVerifier": verifier, "code": code_in(mail)}
    else:
        response = await browser.post("/auth/app/link", json={"token": token_in(mail)})
        assert response.status_code == 200, response.text
        redirect = response.json()["redirectTo"]
        assert redirect.startswith("http://127.0.0.1:53682/callback?")
        auth_code = parse_qs(urlsplit(redirect).query)["code"][0]
        body = {"codeVerifier": verifier, "authorizationCode": auth_code}
    response = await app_client.post("/auth/app/token", json=body)
    assert response.status_code == 200, response.text
    grant = response.json()
    token, device_id = grant["deviceToken"], grant["deviceId"]
    if not register:
        return EnrolledMac(id=device_id, token=token, valkey_user="", valkey_password=None)
    response = await app_client.post(
        "/device/register",
        json={"appVersion": "0.4.0", "macosVersion": "15.5"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    reg = response.json()
    return EnrolledMac(
        id=device_id,
        token=token,
        valkey_user=reg["valkeyUsername"],
        valkey_password=reg["valkeyPassword"],
        s3=reg["s3"],
    )
