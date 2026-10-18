"""
Devices: the app's sign-in, the bootstrap, heartbeat, revocation with what it owes on each
disk's instance, and the device token's refusals.
"""

import hashlib
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.background import expire_stale_rows, reconcile_instances
from floppy_server.context import Context, utcnow
from floppy_server.crypto import token_hash
from floppy_server.models import AuditEvent, Device, KeyExposure, MetaUser, SignInRequest
from tests.conftest import ClientFactory, Org, make_settings
from tests.helpers import (
    code_in,
    create_disk,
    enrol_device,
    fake_valkey,
    instance_url,
    invite_and_join,
    mailer,
    pkce,
    token_in,
)

BEAT = {"appVersion": "0.4.0", "macosVersion": "15.5"}


async def test_the_bootstrap_registers_a_user_on_each_visible_disk(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    disk = await create_disk(admin, ctx, "Progetti")
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    assert device.valkey_user == "floppy_d_" + device.id.replace("-", "")
    assert device.valkey_password
    users = fake_valkey(ctx).users[instance_url(ctx, disk["id"])]
    assert users[device.valkey_user] == (
        hashlib.sha256(device.valkey_password.encode()).digest(),
        "write",
    )
    listed = (await admin.get("/devices")).json()
    assert len(listed) == 1
    assert listed[0]["state"] == "active"
    assert listed[0]["name"] == "MacBook Pro di Giulia"
    assert listed[0]["owner"]["name"] == "Giulia"
    assert listed[0]["valkeyUser"] == device.valkey_user
    assert listed[0]["connected"] is True
    assert "valkeyPassword" not in listed[0]

    config = (await app.get("/device/config", headers=device.auth)).json()
    assert config == {
        "valkeyUsername": device.valkey_user,
        "storageMode": "manual",
        "s3AccessKeyTail": None,
        "metaCa": None,
        "organization": {"id": str(org.id), "name": "Studio studio-a"},
        "user": {"email": org.admin_email, "name": "Giulia"},
        "disks": [
            {
                "id": disk["id"],
                "name": "Progetti",
                "volumeName": "progetti",
                "access": "write",
                "metaUrl": f"{instance_url(ctx, disk['id'])}/1",
                "region": disk["region"],
                "cacheExpireDays": 30,
                "mountPath": "/Volumes/Floppy/Progetti",
            }
        ],
    }
    registered = await app.post("/device/register", json=BEAT, headers=device.auth)
    body = registered.json()
    assert body["metaCa"] is None
    assert (body["organization"], body["user"], body["storageMode"], body["s3"]) == (
        config["organization"],
        config["user"],
        "manual",
        None,
    )
    async with owner_engine.connect() as conn:
        stored = (await conn.execute(select(Device.token_hash, Device.state))).one()
        actions = (
            await conn.execute(select(AuditEvent.action, AuditEvent.detail).order_by(AuditEvent.id))
        ).all()
    assert stored == (token_hash(device.token), "active")
    assert ("device_registered", {"appVersion": "0.4.0", "macosVersion": "15.5"}) in actions
    assert ("signed_in", {"via": "link", "kind": "app"}) in actions


async def test_enrolment_by_code_typed_in_the_app(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    device = await enrol_device(clients(), admin, ctx, org.admin_email, via_code=True)
    assert device.valkey_password


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


async def test_an_app_restarted_within_the_minute_takes_over_its_pending_sign_in(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    app = clients()
    _, first_challenge = pkce()
    request = {
        "email": org.admin_email,
        "codeChallenge": first_challenge,
        "redirectUri": "http://127.0.0.1:53682/callback",
        "deviceName": "MacBook Pro di Giulia",
    }
    first = (await app.post("/auth/app/sign-in", json=request)).json()
    sent = len(mailer(ctx).outbox)
    # The app restarts: a new verifier, a new loopback port, too early for another mail.
    verifier, challenge = pkce()
    again = await app.post(
        "/auth/app/sign-in",
        json={**request, "codeChallenge": challenge, "redirectUri": "http://127.0.0.1:53999/cb"},
    )
    assert again.status_code == 200
    assert again.json()["requestId"] == first["requestId"]
    assert len(mailer(ctx).outbox) == sent
    # The code of the mail already sent completes it with the new verifier...
    code = code_in(mailer(ctx).last_to(org.admin_email))
    done = await app.post("/auth/app/token", json={"codeVerifier": verifier, "code": code})
    assert done.status_code == 200, done.text


async def test_the_link_after_a_restart_redirects_to_the_new_loopback(
    org: Org, ctx: Context, clients: ClientFactory
) -> None:
    app, browser = clients(), clients()
    _, old = pkce()
    request = {
        "email": org.admin_email,
        "codeChallenge": old,
        "redirectUri": "http://127.0.0.1:53682/callback",
        "deviceName": "Mac",
    }
    await app.post("/auth/app/sign-in", json=request)
    verifier, new = pkce()
    await app.post(
        "/auth/app/sign-in",
        json={**request, "codeChallenge": new, "redirectUri": "http://127.0.0.1:54000/cb"},
    )
    link = await browser.post(
        "/auth/app/link", json={"token": token_in(mailer(ctx).last_to(org.admin_email))}
    )
    redirect = link.json()["redirectTo"]
    assert redirect.startswith("http://127.0.0.1:54000/cb?")
    auth_code = redirect.split("code=")[1]
    done = await app.post(
        "/auth/app/token", json={"codeVerifier": verifier, "authorizationCode": auth_code}
    )
    assert done.status_code == 200


async def test_reregistering_issues_a_new_password_on_every_instance(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disks = [await create_disk(admin, ctx, name) for name in ("Progetti", "Archivio")]
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    again = await app.post("/device/register", json=BEAT, headers=device.auth)
    assert again.status_code == 200
    password = again.json()["valkeyPassword"]
    assert password != device.valkey_password
    digest = hashlib.sha256(password.encode()).digest()
    for disk in disks:
        stored = fake_valkey(ctx).users[instance_url(ctx, disk["id"])][device.valkey_user]
        assert stored == (digest, "write")
    assert device.valkey_user in fake_valkey(ctx).killed  # old connections closed


async def test_registering_while_an_instance_is_down_finishes_with_the_reconciler(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk = await create_disk(admin, ctx, "Progetti")
    url = instance_url(ctx, disk["id"])
    fake_valkey(ctx).down.add(url)
    device = await enrol_device(clients(), admin, ctx, org.admin_email)
    assert device.valkey_password
    assert [d["name"] for d in device.disks] == ["Progetti"]
    assert url not in fake_valkey(ctx).users
    fake_valkey(ctx).down.clear()
    await reconcile_instances(ctx, org.id)
    assert fake_valkey(ctx).access(url, device.valkey_user) == "write"


async def test_heartbeat_reports_status_and_logs_an_outdated_app_once(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    beat = {
        "appVersion": "0.3.9",
        "macosVersion": "15.5",
        "replica": {"mode": "replica", "lagMs": 12},
        "s3AccessKeyTail": "00001",
        "mountedDiskIds": [],
    }
    for _ in range(2):
        response = await app.post("/device/heartbeat", json=beat, headers=device.auth)
        assert response.json() == {"state": "active", "disks": []}
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


async def test_admin_revokes_a_device_and_the_window_of_each_disk_it_saw_opens(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    seen = await create_disk(
        admin, ctx, "Progetti", access=[{"personId": person["id"], "access": "read"}]
    )
    unseen = await create_disk(admin, ctx, "Riservato")
    app = clients()
    device = await enrol_device(app, marco, ctx, "marco@studio-a.test", name="iMac di Marco")
    response = await admin.post(f"/devices/{device.id}/revoke")
    assert response.status_code == 200
    body = response.json()
    assert body["device"]["state"] == "revoked"
    assert body["device"]["revokedBy"] == "Giulia"
    assert body["device"]["connected"] is False
    windows = {d["diskName"]: d["window"] for d in body["key"]["disks"]}
    assert [r["label"] for r in windows["Progetti"]["reasons"]] == ["iMac di Marco"]
    assert windows["Riservato"] is None
    url = instance_url(ctx, seen["id"])
    assert fake_valkey(ctx).access(url, device.valkey_user) is None
    assert device.valkey_user in fake_valkey(ctx).killed
    assert device.valkey_user not in fake_valkey(ctx).users.get(instance_url(ctx, unseen["id"]), {})
    again = await admin.post(f"/devices/{device.id}/revoke")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")
    # A revoked device token no longer configures anything; the heartbeat says why.
    refused = await app.get("/device/config", headers=device.auth)
    assert (refused.status_code, refused.json()["details"]) == (401, {"reason": "device_revoked"})
    beat = await app.post("/device/heartbeat", json=BEAT, headers=device.auth)
    assert beat.json() == {"state": "revoked", "disks": []}
    async with owner_engine.connect() as conn:
        reason = (await conn.execute(select(Device.revoke_reason))).scalar_one()
    assert reason == "admin"


async def test_members_see_and_revoke_only_their_own_devices(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    await create_disk(
        admin, ctx, "Progetti", access=[{"personId": person["id"], "access": "write"}]
    )
    giulia_device = await enrol_device(clients(), admin, ctx, org.admin_email)
    marco_device = await enrol_device(
        clients(), marco, ctx, "marco@studio-a.test", name="iMac di Marco"
    )
    seen = [d["id"] for d in (await marco.get("/devices")).json()]
    assert seen == [marco_device.id]
    other = await marco.post(f"/devices/{giulia_device.id}/revoke")
    assert (other.status_code, other.json()["code"]) == (404, "not_found")
    own = await marco.post(f"/devices/{marco_device.id}/revoke")
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
    disk = await create_disk(admin, ctx, "Progetti")
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    response = await app.post("/device/sign-out", headers=device.auth)
    assert response.status_code == 204
    assert fake_valkey(ctx).access(instance_url(ctx, disk["id"]), device.valkey_user) is None
    key = (await admin.get("/key")).json()
    assert key["disks"][0]["window"] is None
    refused = await app.get("/device/config", headers=device.auth)
    assert refused.json()["details"] == {"reason": "device_signed_out"}
    async with owner_engine.connect() as conn:
        actions = (
            (await conn.execute(select(AuditEvent.action).order_by(AuditEvent.id))).scalars().all()
        )
        reason = (await conn.execute(select(Device.revoke_reason))).scalar_one()
    assert actions[-1] == "device_signed_out"
    assert reason == "signed_out"


async def test_revoking_a_pending_device_opens_no_window(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    await create_disk(admin, ctx, "Progetti")
    device = await enrol_device(clients(), admin, ctx, org.admin_email, register=False)
    response = await admin.post(f"/devices/{device.id}/revoke")
    assert response.json()["key"]["disks"][0]["window"] is None
    refused = await clients().get(
        "/device/config", headers={"Authorization": f"Bearer {device.token}"}
    )
    assert refused.json()["details"] == {"reason": "device_revoked"}


async def test_a_pending_device_is_told_it_is_not_registered(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email, register=False)
    refused = await app.get("/device/config", headers=device.auth)
    assert (refused.status_code, refused.json()["details"]) == (
        401,
        {"reason": "device_not_registered"},
    )


async def test_revocation_while_an_instance_is_down_is_finished_by_the_reconciler(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    disk = await create_disk(admin, ctx, "Progetti")
    url = instance_url(ctx, disk["id"])
    device = await enrol_device(clients(), admin, ctx, org.admin_email)
    valkey = fake_valkey(ctx)
    valkey.down.add(url)
    response = await admin.post(f"/devices/{device.id}/revoke")
    assert response.status_code == 200
    assert valkey.access(url, device.valkey_user) == "write"
    async with owner_engine.connect() as conn:
        owed = (await conn.execute(select(MetaUser.access, MetaUser.applied_at))).one()
    assert tuple(owed) == ("none", None)
    valkey.down.clear()
    await reconcile_instances(ctx, org.id)
    assert valkey.access(url, device.valkey_user) is None
    async with owner_engine.connect() as conn:
        done = (await conn.execute(select(MetaUser.applied_at))).scalar_one()
    assert done is not None


async def test_pending_devices_expire_after_a_day(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    device = await enrol_device(clients(), admin, ctx, org.admin_email, register=False)
    async with owner_engine.begin() as conn:
        await conn.execute(update(Device).values(created_at=utcnow() - timedelta(hours=25)))
    await expire_stale_rows(ctx)
    listed = (await admin.get("/devices")).json()[0]
    assert (listed["id"], listed["state"], listed["revokedBy"]) == (device.id, "revoked", None)
    async with owner_engine.connect() as conn:
        reason = (await conn.execute(select(Device.revoke_reason))).scalar_one()
        logged = (
            await conn.execute(
                select(AuditEvent.actor_kind, AuditEvent.detail).where(
                    AuditEvent.action == "device_revoked"
                )
            )
        ).one()
    assert reason == "never_registered"
    assert tuple(logged) == ("system", {"reason": "never_registered"})


async def test_device_endpoints_need_a_valid_token(client: httpx.AsyncClient, org: Org) -> None:
    for response in (
        await client.get("/device/config"),
        await client.get("/device/config", headers={"Authorization": "Bearer nope"}),
        await client.post("/device/heartbeat", json=BEAT),
    ):
        assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
        assert response.json()["details"] == {"reason": "token_unknown"}


CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA\n-----END CERTIFICATE-----"


async def test_the_configuration_carries_the_metadata_ca_when_private(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory, tmp_path: Path
) -> None:
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    # As the environment gives it: one line, newlines escaped.
    ctx.settings = make_settings(meta_ca_pem=CA_PEM.replace("\n", "\\n"))
    config = (await app.get("/device/config", headers=device.auth)).json()
    assert config["metaCa"] == CA_PEM + "\n"
    registered = (await app.post("/device/register", json=BEAT, headers=device.auth)).json()
    assert registered["metaCa"] == CA_PEM + "\n"
    assert config["valkeyUsername"] == device.valkey_user
    path = tmp_path / "meta-ca.pem"
    path.write_text(CA_PEM + "\n")
    ctx.settings = make_settings(meta_ca_file=str(path))
    assert (await app.get("/device/config", headers=device.auth)).json()["metaCa"] == CA_PEM + "\n"


def test_the_metadata_ca_is_checked_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="certificates only"):
        make_settings(meta_ca_pem="-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----")
    with pytest.raises(ValidationError, match="certificates only"):
        make_settings(meta_ca_pem="not a certificate")
    with pytest.raises(ValidationError, match="cannot be read"):
        make_settings(meta_ca_file=str(tmp_path / "missing.pem"))
    with pytest.raises(ValidationError, match="not both"):
        make_settings(meta_ca_pem=CA_PEM, meta_ca_file=str(tmp_path / "x.pem"))
    assert make_settings(meta_ca_pem="  ").meta_ca is None
