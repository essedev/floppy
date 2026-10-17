"""The bootstrap, the settings' guards, and the page the app's magic link opens."""

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.bootstrap import BootstrapError, BootstrapInput, bootstrap
from floppy_server.config import Environment, MailBackend
from floppy_server.context import Context
from floppy_server.crypto import SecretBox
from floppy_server.models import Membership, S3Key
from floppy_server.rekey import reencrypt_all
from tests.conftest import ClientFactory, Org, make_settings
from tests.helpers import mailer, pkce, token_in


def bootstrap_input(**overrides: object) -> BootstrapInput:
    values: dict[str, object] = {
        "organization_name": "Studio Esempio",
        "slug": "studio-esempio",
        "admin_email": "Admin@Studio.test",
        "s3_endpoint": "https://s3.test",
        "s3_region": "eu-south",
        "s3_bucket": "floppy-studio",
        "s3_access_key_id": "AKIABOOTSTRAP0001",
        "s3_secret_key": "bootstrap-secret-0001",
        "valkey_url": "redis://meta.studio.test:6379",
    }
    values.update(overrides)
    return BootstrapInput(**values)  # type: ignore[arg-type]


async def test_bootstrap_creates_an_active_admin_and_an_active_key(
    ctx: Context, owner_engine: AsyncEngine
) -> None:
    await bootstrap(ctx, bootstrap_input())
    async with owner_engine.connect() as conn:
        membership = (await conn.execute(select(Membership.role, Membership.status))).one()
        key = (
            await conn.execute(select(S3Key.state, S3Key.access_key_tail, S3Key.secret_key_enc))
        ).one()
        email = (await conn.execute(text("SELECT email FROM users"))).scalar_one()
    assert tuple(membership) == ("admin", "active")
    assert (key[0], key[1]) == ("active", "P0001")
    assert key[2] is not None
    assert b"bootstrap-secret" not in key[2]
    assert email == "admin@studio.test"


async def test_bootstrap_refusals(ctx: Context) -> None:
    with pytest.raises(BootstrapError, match="test write"):
        await bootstrap(ctx, bootstrap_input(s3_access_key_id="AKIADENIEDKEY0001"))
    with pytest.raises(BootstrapError, match="no credentials"):
        await bootstrap(ctx, bootstrap_input(valkey_url="redis://user:pw@meta.test:6379"))
    ctx.settings.single_organization = True
    await bootstrap(ctx, bootstrap_input())
    with pytest.raises(BootstrapError, match="already has its organization"):
        await bootstrap(ctx, bootstrap_input(slug="second", s3_bucket="other"))


def test_production_refuses_development_backends() -> None:
    with pytest.raises(ValidationError, match="console mail"):
        make_settings(env=Environment.PRODUCTION, mail_backend=MailBackend.CONSOLE)
    with pytest.raises(ValidationError, match="FLOPPY_SECRET_KEY"):
        make_settings(secret_key=None)


async def test_the_app_link_page_completes_only_on_the_button(
    org: Org, ctx: Context, clients: ClientFactory
) -> None:
    app, browser = clients(), clients()
    verifier, challenge = pkce()
    await app.post(
        "/auth/app/sign-in",
        json={
            "email": org.admin_email,
            "codeChallenge": challenge,
            "redirectUri": "http://127.0.0.1:53682/callback",
            "deviceName": "MacBook Pro di Giulia",
        },
    )
    mail = mailer(ctx).last_to(org.admin_email)
    assert "http://localhost:4508/api/v1/auth/app/link?token=" in mail.text
    assert "MacBook Pro di Giulia" in mail.text
    token = token_in(mail)
    # Opening the page (a mail scanner would too) changes nothing.
    page = await browser.get("/auth/app/link", params={"token": token})
    assert page.status_code == 200
    assert "Continua" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert (await browser.get("/auth/app/link", params={"token": token})).status_code == 200
    pressed = await browser.post("/auth/app/link/continue", data={"token": token})
    assert pressed.status_code == 303
    location = pressed.headers["location"]
    assert location.startswith("http://127.0.0.1:53682/callback?code=")
    code = location.split("code=")[1]
    exchanged = await app.post(
        "/auth/app/token", json={"codeVerifier": verifier, "authorizationCode": code}
    )
    assert exchanged.status_code == 200
    again = await browser.post("/auth/app/link/continue", data={"token": token})
    assert again.status_code == 410
    assert "non vale più" in again.text


async def test_reencrypt_moves_every_secret_to_the_newest_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    await admin.patch(
        "/settings",
        json={
            "mail": {"host": "smtp.test", "port": 25, "sender": "f@x.test", "password": "smtp-pw"}
        },
    )
    ctx.box = SecretBox({1: ctx.settings.key_ring[1], 2: bytes(range(32, 64))})
    assert await reencrypt_all(ctx) == 2
    assert await reencrypt_all(ctx) == 0
    async with owner_engine.connect() as conn:
        keys = list((await conn.execute(select(S3Key.secret_key_enc))).scalars())
        smtp = list(
            (await conn.execute(text("SELECT smtp_password_enc FROM organizations"))).scalars()
        )
    assert [blob[1] for blob in keys + smtp] == [2, 2]
    async with ctx.sessions() as db:
        key = (await db.execute(select(S3Key))).scalar_one()
    assert ctx.credentials(key).secret_key == "first-secret-key-value-0001"


async def test_openapi_is_served(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert "/api/v1/people" in schema["paths"]
