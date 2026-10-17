"""Macs: the app's sign-in, enrolment, heartbeat, revocation and the ACL reconciler."""

import hashlib
from datetime import timedelta

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.background import expire_stale_rows, reconcile_acl
from floppy_server.context import Context, utcnow
from floppy_server.crypto import token_hash
from floppy_server.infra.metadata import FakeMetadataAdmin
from floppy_server.models import AuditEvent, Device, KeyExposure, SignInRequest
from tests.conftest import ClientFactory, Org
from tests.helpers import code_in, enrol_mac, invite_and_join, mailer, pkce, token_in


def fake_valkey(ctx: Context) -> FakeMetadataAdmin:
    assert isinstance(ctx.metadata, FakeMetadataAdmin)
    return ctx.metadata


async def test_enrolment_by_link_registers_a_valkey_user(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email)
    assert mac.valkey_user == "floppy_d_" + mac.id.replace("-", "")
    assert mac.valkey_password
    users = fake_valkey(ctx).users[org.valkey_url]
    assert users[mac.valkey_user] == hashlib.sha256(mac.valkey_password.encode()).digest()

    macs = (await admin.get("/devices")).json()
    assert len(macs) == 1
    listed = macs[0]
    assert listed["state"] == "active"
    assert listed["name"] == "MacBook Pro di Giulia"
    assert listed["owner"]["name"] == "Giulia"
    assert listed["valkeyUser"] == mac.valkey_user
    assert listed["appVersion"] == "0.4.0"
    assert listed["connected"] is True
    assert "valkeyPassword" not in listed

    config = await app.get("/device/config", headers=mac.auth)
    assert config.json() == {
        "valkeyUrl": org.valkey_url,
        "keyScope": "team",
        "s3AccessKeyTail": None,
        "disks": [],
    }
    async with owner_engine.connect() as conn:
        stored = (await conn.execute(select(Device.token_hash, Device.state))).one()
        actions = (
            await conn.execute(select(AuditEvent.action, AuditEvent.detail).order_by(AuditEvent.id))
        ).all()
    assert stored == (token_hash(mac.token), "active")
    assert ("device_registered", {"appVersion": "0.4.0", "macosVersion": "15.5"}) in actions
    assert ("signed_in", {"via": "link", "kind": "app"}) in actions


async def test_enrolment_by_code_typed_in_the_app(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    mac = await enrol_mac(clients(), admin, ctx, org.admin_email, via_code=True)
    assert mac.valkey_password


async def test_authorization_code_is_single_use_and_bound_to_the_verifier(
    org: Org, ctx: Context, clients: ClientFactory
) -> None:
    app, browser = clients(), clients()
    verifier, challenge = pkce()
    await app.post(
        "/auth/app/sign-in",
        json={
            "email": org.admin_email,
            "codeChallenge": challenge,
            "redirectUri": "http://127.0.0.1:53682/cb",
            "deviceName": "Mac",
        },
    )
    link = await browser.post(
        "/auth/app/link", json={"token": token_in(mailer(ctx).last_to(org.admin_email))}
    )
    auth_code = link.json()["redirectTo"].split("code=")[1]
    other_verifier, _ = pkce()
    wrong = await app.post(
        "/auth/app/token", json={"codeVerifier": other_verifier, "authorizationCode": auth_code}
    )
    assert wrong.json()["code"] == "link_invalid"
    right = await app.post(
        "/auth/app/token", json={"codeVerifier": verifier, "authorizationCode": auth_code}
    )
    assert right.status_code == 200
    again = await app.post(
        "/auth/app/token", json={"codeVerifier": verifier, "authorizationCode": auth_code}
    )
    assert again.json()["code"] == "link_invalid"
    # The app's link never opens a web session.
    assert (await browser.get("/session")).status_code == 401


async def test_authorization_code_expires_after_a_minute(
    org: Org, ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    app = clients()
    verifier, challenge = pkce()
    await app.post(
        "/auth/app/sign-in",
        json={
            "email": org.admin_email,
            "codeChallenge": challenge,
            "redirectUri": "http://127.0.0.1:1/",
            "deviceName": "Mac",
        },
    )
    link = await clients().post(
        "/auth/app/link", json={"token": token_in(mailer(ctx).last_to(org.admin_email))}
    )
    auth_code = link.json()["redirectTo"].split("code=")[1]
    async with owner_engine.begin() as conn:
        await conn.execute(
            update(SignInRequest).values(auth_code_expires_at=utcnow() - timedelta(seconds=1))
        )
    late = await app.post(
        "/auth/app/token", json={"codeVerifier": verifier, "authorizationCode": auth_code}
    )
    assert (late.status_code, late.json()["code"]) == (410, "link_invalid")


async def test_app_code_lockout(org: Org, ctx: Context, clients: ClientFactory) -> None:
    app = clients()
    verifier, challenge = pkce()
    await app.post(
        "/auth/app/sign-in",
        json={
            "email": org.admin_email,
            "codeChallenge": challenge,
            "redirectUri": "http://127.0.0.1:2/",
            "deviceName": "Mac",
        },
    )
    right = code_in(mailer(ctx).last_to(org.admin_email))
    wrong = "000000" if right != "000000" else "111111"
    codes = [
        (await app.post("/auth/app/token", json={"codeVerifier": verifier, "code": wrong})).json()[
            "code"
        ]
        for _ in range(5)
    ]
    assert codes == ["invalid_code"] * 4 + ["request_locked"]
    final = await app.post("/auth/app/token", json={"codeVerifier": verifier, "code": right})
    assert final.json()["code"] == "request_locked"


async def test_app_sign_in_refuses_a_redirect_off_the_loopback(
    org: Org, client: httpx.AsyncClient
) -> None:
    _, challenge = pkce()
    response = await client.post(
        "/auth/app/sign-in",
        json={
            "email": org.admin_email,
            "codeChallenge": challenge,
            "redirectUri": "https://evil.test/",
            "deviceName": "Mac",
        },
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation")


async def test_reregistering_issues_a_new_password(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email)
    again = await app.post(
        "/device/register", json={"appVersion": "0.4.0", "macosVersion": "15.5"}, headers=mac.auth
    )
    assert again.status_code == 200
    assert again.json()["valkeyPassword"] != mac.valkey_password
    stored = fake_valkey(ctx).users[org.valkey_url][mac.valkey_user]
    assert stored == hashlib.sha256(again.json()["valkeyPassword"].encode()).digest()


async def test_registering_while_valkey_is_down_keeps_the_mac_pending(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email, register=False)
    fake_valkey(ctx).down = True
    response = await app.post(
        "/device/register", json={"appVersion": "0.4.0", "macosVersion": "15.5"}, headers=mac.auth
    )
    assert (response.status_code, response.json()["code"]) == (503, "metadata_unavailable")
    macs = (await admin.get("/devices")).json()
    assert macs[0]["state"] == "pending"


async def test_heartbeat_reports_status_and_logs_an_outdated_app_once(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email)
    beat = {
        "appVersion": "0.3.9",
        "macosVersion": "15.5",
        "replica": {"mode": "replica", "lagMs": 12},
        "s3AccessKeyTail": "00001",
        "mountedDiskIds": [],
    }
    for _ in range(2):
        response = await app.post("/device/heartbeat", json=beat, headers=mac.auth)
        assert response.json() == {"state": "active"}
    listed = (await admin.get("/devices")).json()[0]
    assert listed["appOutdated"] is True
    assert listed["replica"] == {"mode": "replica", "lagMs": 12}
    async with owner_engine.connect() as conn:
        outdated = (
            (
                await conn.execute(
                    select(AuditEvent.detail).where(AuditEvent.action == "device_seen_outdated")
                )
            )
            .scalars()
            .all()
        )
    assert outdated == [{"appVersion": "0.3.9"}]


async def test_admin_revokes_a_mac_and_the_window_opens(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    app = clients()
    mac = await enrol_mac(app, marco, ctx, "marco@studio-a.test", name="iMac di Marco")
    response = await admin.post(f"/devices/{mac.id}/revoke")
    assert response.status_code == 200
    body = response.json()
    assert body["device"]["state"] == "revoked"
    assert body["device"]["revokedBy"] == "Giulia"
    assert body["device"]["connected"] is False
    assert [r["label"] for r in body["key"]["window"]["reasons"]] == ["iMac di Marco"]
    assert mac.valkey_user not in fake_valkey(ctx).users[org.valkey_url]
    assert mac.valkey_user in fake_valkey(ctx).killed
    again = await admin.post(f"/devices/{mac.id}/revoke")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")
    # A revoked device token no longer configures anything; the heartbeat says why.
    assert (await app.get("/device/config", headers=mac.auth)).status_code == 401
    beat = await app.post(
        "/device/heartbeat", json={"appVersion": "0.4.0", "macosVersion": "15.5"}, headers=mac.auth
    )
    assert beat.json() == {"state": "revoked"}
    async with owner_engine.connect() as conn:
        reason = (await conn.execute(select(Device.revoke_reason))).scalar_one()
    assert reason == "admin"


async def test_members_see_and_revoke_only_their_own_macs(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    giulia_mac = await enrol_mac(clients(), admin, ctx, org.admin_email)
    marco_mac = await enrol_mac(clients(), marco, ctx, "marco@studio-a.test", name="iMac di Marco")
    seen = [m["id"] for m in (await marco.get("/devices")).json()]
    assert seen == [marco_mac.id]
    other = await marco.post(f"/devices/{giulia_mac.id}/revoke")
    assert (other.status_code, other.json()["code"]) == (404, "not_found")
    own = await marco.post(f"/devices/{marco_mac.id}/revoke")
    assert own.status_code == 200
    assert own.json()["key"] is None
    session = (await marco.get("/session")).json()
    assert session["organization"]["deviceCount"] == 0
    async with owner_engine.connect() as conn:
        reason = (
            await conn.execute(select(Device.revoke_reason).where(Device.state == "revoked"))
        ).scalar_one()
        exposures = (await conn.execute(select(KeyExposure.kind))).scalars().all()
    assert reason == "owner"
    assert exposures == ["device_revoked"]


async def test_signing_out_from_the_app_opens_no_window(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email)
    response = await app.post("/device/sign-out", headers=mac.auth)
    assert response.status_code == 204
    assert mac.valkey_user not in fake_valkey(ctx).users[org.valkey_url]
    key = (await admin.get("/key")).json()
    assert key["window"] is None
    async with owner_engine.connect() as conn:
        actions = (
            (await conn.execute(select(AuditEvent.action).order_by(AuditEvent.id))).scalars().all()
        )
        reason = (await conn.execute(select(Device.revoke_reason))).scalar_one()
    assert actions[-1] == "device_signed_out"
    assert reason == "signed_out"


async def test_revoking_a_pending_mac_opens_no_window(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    mac = await enrol_mac(clients(), admin, ctx, org.admin_email, register=False)
    response = await admin.post(f"/devices/{mac.id}/revoke")
    assert response.json()["key"]["window"] is None


async def test_revocation_while_valkey_is_down_is_finished_by_the_reconciler(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    mac = await enrol_mac(clients(), admin, ctx, org.admin_email)
    valkey = fake_valkey(ctx)
    valkey.down = True
    response = await admin.post(f"/devices/{mac.id}/revoke")
    assert response.status_code == 200
    assert mac.valkey_user in valkey.users[org.valkey_url]
    async with owner_engine.connect() as conn:
        owed = (await conn.execute(select(Device.valkey_user_deleted_at))).scalar_one()
    assert owed is None
    valkey.down = False
    await reconcile_acl(ctx, org.id)
    assert mac.valkey_user not in valkey.users[org.valkey_url]
    async with owner_engine.connect() as conn:
        done = (await conn.execute(select(Device.valkey_user_deleted_at))).scalar_one()
    assert done is not None


async def test_reconciler_rebuilds_users_lost_by_valkey(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    mac = await enrol_mac(clients(), admin, ctx, org.admin_email)
    valkey = fake_valkey(ctx)
    digest = valkey.users[org.valkey_url][mac.valkey_user]
    # A failover to a replica: ACL users are not replicated; a stray user appears too.
    valkey.users[org.valkey_url] = {"floppy_d_" + "f" * 32: b"x" * 32}
    await reconcile_acl(ctx, org.id)
    assert valkey.users[org.valkey_url] == {mac.valkey_user: digest}


async def test_pending_macs_expire_after_a_day(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    mac = await enrol_mac(clients(), admin, ctx, org.admin_email, register=False)
    async with owner_engine.begin() as conn:
        await conn.execute(update(Device).values(created_at=utcnow() - timedelta(hours=25)))
    await expire_stale_rows(ctx)
    listed = (await admin.get("/devices")).json()[0]
    assert (listed["id"], listed["state"], listed["revokedBy"]) == (mac.id, "revoked", None)
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT revoke_reason, valkey_user_deleted_at IS NOT NULL FROM devices")
            )
        ).one()
        logged = (
            await conn.execute(
                select(AuditEvent.actor_kind, AuditEvent.detail).where(
                    AuditEvent.action == "device_revoked"
                )
            )
        ).one()
    assert tuple(row) == ("never_registered", True)
    assert tuple(logged) == ("system", {"reason": "never_registered"})


async def test_device_endpoints_need_a_valid_token(client: httpx.AsyncClient, org: Org) -> None:
    for response in (
        await client.get("/device/config"),
        await client.get("/device/config", headers={"Authorization": "Bearer nope"}),
        await client.post("/device/heartbeat", json={"appVersion": "1", "macosVersion": "1"}),
    ):
        assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
