"""
Storage modes and key scopes (DATABASE_SCHEMA.md, "Storage modes"), the mail language and
`organization_created`. The provider is the labelled `FakeAdapter`; the real Garage adapter is
tested in test_garage.py.
"""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.disks.service import run_disk_formats
from floppy_server.infra.juicefs import FakeVolumeFormatter
from floppy_server.infra.providers import FakeAdapter
from floppy_server.keys.scoped import delete_owed_keys
from floppy_server.keys.service import rotate_after_revocations, run_rotations
from floppy_server.models import AuditEvent, KeyExposure, Organization, S3Key
from tests.conftest import ClientFactory, Org, make_org
from tests.helpers import enrol_mac, invite_and_join, mailer, sign_in, token_in

PERSON_KEY = {"accessKeyId": "AKIAPERSONKEY00011", "secretKey": "person-secret-0011"}


def adapter(ctx: Context) -> FakeAdapter:
    if ctx.adapter_override is None:
        ctx.adapter_override = FakeAdapter()
    assert isinstance(ctx.adapter_override, FakeAdapter)
    return ctx.adapter_override


async def keys(engine: AsyncEngine) -> list[tuple[Any, ...]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(
                S3Key.holder, S3Key.access_key_tail, S3Key.state, S3Key.secret_key_enc.is_not(None)
            ).order_by(S3Key.created_at)
        )
        return [tuple(row) for row in rows]


async def actions(engine: AsyncEngine) -> list[tuple[str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(AuditEvent.action, AuditEvent.detail).order_by(AuditEvent.id)
        )
        return [(a, d) for a, d in rows]


# The mail language


async def test_mail_follows_the_organizations_language(
    ctx: Context, clients: ClientFactory
) -> None:
    org = await make_org(ctx, "studio-en", "anne@en.test", mail_language="en")
    admin = clients()
    await sign_in(admin, ctx, org.admin_email)
    assert "to sign in to Floppy" in mailer(ctx).last_to(org.admin_email).text
    await admin.post("/people", json={"email": "bob@en.test", "role": "member"})
    assert "invited you to" in mailer(ctx).last_to("bob@en.test").text
    updated = await admin.patch("/settings", json={"mailLanguage": "it"})
    assert updated.json()["mailLanguage"] == "it"
    sent = await admin.post("/settings/mail/test")
    assert sent.status_code == 200
    assert "prova" in mailer(ctx).last_to(org.admin_email).subject


async def test_an_unknown_address_gets_the_browsers_language(
    org: Org, ctx: Context, client: httpx.AsyncClient
) -> None:
    await client.post(
        "/auth/sign-in",
        json={"email": "who@else.test"},
        headers={"Accept-Language": "en-GB,en;q=0.9"},
    )
    assert "belongs to no team" in mailer(ctx).last_to("who@else.test").text
    await client.post(
        "/auth/sign-in",
        json={"email": "chi@else.test"},
        headers={"Accept-Language": "de, it;q=0.5"},
    )
    assert "nessun team" in mailer(ctx).last_to("chi@else.test").text


async def test_bootstrap_logs_organization_created(org: Org, owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                select(AuditEvent.actor_kind, AuditEvent.category, AuditEvent.detail).where(
                    AuditEvent.action == "organization_created"
                )
            )
        ).one()
    assert tuple(row) == ("operator", "settings", {"storageMode": "manual", "provider": ""})


# Manual, one key per person


@pytest.fixture
async def person_org(ctx: Context) -> Org:
    return await make_org(ctx, "studio-p", "paola@p.test", storage_key_scope="person")


async def test_person_scope_formats_with_the_control_planes_key_and_no_key_inside(
    person_org: Org, ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    admin = clients()
    await sign_in(admin, ctx, person_org.admin_email)
    await admin.post("/disks", json={"name": "Archivio"})
    await run_disk_formats(ctx)
    assert isinstance(ctx.formatter, FakeVolumeFormatter)
    assert ctx.formatter.formats[(person_org.valkey_url, 1)]["access_key_id"] == ""
    assert (await keys(owner_engine))[0][:3] == ("control_plane", "00001", "active")
    rotation = await admin.post(
        "/key/rotations", json={"accessKeyId": "AKIANEWTEAMKEY0002", "secretKey": "x" * 12}
    )
    assert (rotation.status_code, rotation.json()["code"]) == (409, "invalid_state")


async def test_person_keys_are_pasted_probed_given_to_macs_and_replaced(
    person_org: Org, ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    admin = clients()
    await sign_in(admin, ctx, person_org.admin_email)
    missing = await admin.post("/people", json={"email": "marco@p.test", "role": "member"})
    assert (missing.status_code, missing.json()["details"]) == (422, {"field": "key"})
    denied = await admin.post(
        "/people",
        json={
            "email": "marco@p.test",
            "role": "member",
            "key": {"accessKeyId": "AKIADENIED000099", "secretKey": "denied-secret-99"},
        },
    )
    assert (denied.status_code, denied.json()["code"]) == (422, "probe_failed")
    assert "denied-secret-99" not in denied.text

    marco = clients()
    response = await admin.post(
        "/people", json={"email": "marco@p.test", "role": "member", "key": PERSON_KEY}
    )
    assert response.status_code == 201
    await marco.post(
        "/auth/invitation", json={"token": token_in(mailer(ctx).last_to("marco@p.test"))}
    )
    app = clients()
    mac = await enrol_mac(app, marco, ctx, "marco@p.test")
    assert mac.s3 == PERSON_KEY
    config = (await app.get("/device/config", headers=mac.auth)).json()
    assert (config["keyScope"], config["s3AccessKeyTail"]) == ("person", "00011")

    # A lost Mac: the person's key is reached; no window, the key waits for its deletion.
    revoked = (await admin.post(f"/devices/{mac.id}/revoke")).json()
    assert revoked["key"]["window"] is None
    owed = revoked["key"]["keys"]
    assert [(k["holder"], k["owner"], k["state"]) for k in owed] == [
        ("person", "Marco", "retiring")
    ]
    other = await enrol_mac(clients(), marco, ctx, "marco@p.test", name="Mini")
    assert other.s3 is None

    person_id = response.json()["id"]
    new_key = {"accessKeyId": "AKIAPERSONKEY00022", "secretKey": "person-secret-0022"}
    replaced = (await admin.put(f"/people/{person_id}/key", json=new_key)).json()
    assert [(k["accessKeyTail"], k["state"]) for k in replaced["keys"]] == [
        ("00011", "retiring"),
        ("00022", "active"),
    ]
    fetched = await clients().get("/device/key", headers=other.auth)
    assert fetched.json() == new_key

    old_id = replaced["keys"][0]["id"]
    confirmed = (await admin.post(f"/key/{old_id}/deleted")).json()
    assert [k["accessKeyTail"] for k in confirmed["keys"]] == ["00022"]
    again = await admin.post(f"/key/{old_id}/deleted")
    assert again.json()["code"] == "invalid_state"
    stored = await keys(owner_engine)
    assert ("person", "00011", "retired", False) in stored
    assert ("person", "00022", "active", True) in stored
    logged = await actions(owner_engine)
    assert ("key_deleted", {"holder": "person", "accessKeyTail": "00011", "by": "admin"}) in logged
    assert ("key_created", {"holder": "person", "accessKeyTail": "00022", "for": "Marco"}) in logged

    removed = (await admin.delete(f"/people/{person_id}")).json()
    assert removed["key"]["window"] is None
    assert [(k["accessKeyTail"], k["state"]) for k in removed["key"]["keys"]] == [
        ("00022", "retiring")
    ]
    async with owner_engine.connect() as conn:
        assert (await conn.execute(select(KeyExposure.id))).first() is None


async def test_signing_out_keeps_the_persons_key(
    person_org: Org, ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    admin = clients()
    await sign_in(admin, ctx, person_org.admin_email)
    marco = clients()
    await admin.post("/people", json={"email": "marco@p.test", "role": "member", "key": PERSON_KEY})
    await marco.post(
        "/auth/invitation", json={"token": token_in(mailer(ctx).last_to("marco@p.test"))}
    )
    app = clients()
    mac = await enrol_mac(app, marco, ctx, "marco@p.test")
    await app.post("/device/sign-out", headers=mac.auth)
    assert ("person", "00011", "active", True) in await keys(owner_engine)


# Managed, one key per Mac


@pytest.fixture
async def device_org(ctx: Context) -> Org:
    adapter(ctx)
    return await make_org(
        ctx,
        "studio-d",
        "dario@d.test",
        storage_mode="managed",
        storage_provider="garage",
        storage_key_scope="device",
    )


async def test_managed_bootstrap_creates_the_bucket_and_its_own_key(
    device_org: Org, ctx: Context, owner_engine: AsyncEngine
) -> None:
    fake = adapter(ctx)
    assert fake.buckets == {device_org.bucket}
    async with owner_engine.connect() as conn:
        holder, origin, ref = (
            await conn.execute(select(S3Key.holder, S3Key.origin, S3Key.provider_ref))
        ).one()
    assert (holder, origin) == ("control_plane", "provider")
    assert ref in fake.keys


async def test_each_mac_gets_its_own_key_deleted_when_revoked(
    device_org: Org, ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    fake = adapter(ctx)
    admin = clients()
    await sign_in(admin, ctx, device_org.admin_email)
    app = clients()
    mac = await enrol_mac(app, admin, ctx, device_org.admin_email)
    assert mac.s3 is not None
    assert mac.s3["accessKeyId"] in fake.keys
    async with owner_engine.connect() as conn:
        secret = (
            await conn.execute(select(S3Key.secret_key_enc).where(S3Key.holder == "device"))
        ).scalar_one()
    assert secret is None  # a Mac's own secret is returned once, never kept

    # A reissue (Keychain lost) replaces the key and deletes the old one at the provider.
    again = (
        await app.post(
            "/device/register",
            json={"appVersion": "0.4.0", "macosVersion": "15.5"},
            headers=mac.auth,
        )
    ).json()
    assert again["s3"]["accessKeyId"] != mac.s3["accessKeyId"]
    assert mac.s3["accessKeyId"] in fake.deleted

    fake.down = True
    revoked = (await admin.post(f"/devices/{mac.id}/revoke")).json()
    assert revoked["key"]["window"] is None
    assert [k["state"] for k in revoked["key"]["keys"]] == ["retiring"]
    fake.down = False
    assert await delete_owed_keys(ctx, device_org.id) == 1
    assert again["s3"]["accessKeyId"] in fake.deleted
    stored = [k for k in await keys(owner_engine) if k[0] == "device"]
    assert [k[2] for k in stored] == ["retired", "retired"]
    logged = [d for a, d in await actions(owner_engine) if a == "key_deleted"]
    assert [d["by"] for d in logged] == ["adapter", "adapter"]
    assert (await admin.post("/key/rotations")).json()["code"] == "invalid_state"


async def test_the_schema_holds_the_scopes(device_org: Org, owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_organizations_storage_key_scope_manual"):
            await conn.execute(
                text("UPDATE organizations SET storage_mode = 'manual', storage_provider = NULL")
            )
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_s3_keys_device_holder"):
            await conn.execute(text("UPDATE s3_keys SET holder = 'device'"))


# Managed, a team key the adapter rotates by itself


async def test_managed_team_key_rotates_after_revocations_and_closes_itself(
    ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    fake = adapter(ctx)
    org = await make_org(
        ctx,
        "studio-t",
        "tina@t.test",
        storage_mode="managed",
        storage_provider="garage",
        storage_key_scope="team",
    )
    admin = clients()
    await sign_in(admin, ctx, org.admin_email)
    async with owner_engine.connect() as conn:
        first_ref = (await conn.execute(select(S3Key.provider_ref))).scalar_one()
    pasted = await admin.post(
        "/key/rotations", json={"accessKeyId": "AKIAPASTEDKEY0003", "secretKey": "pasted-0003"}
    )
    assert pasted.json()["details"] == {"field": "accessKeyId"}

    marco = clients()
    await invite_and_join(admin, marco, ctx, "marco@t.test")
    mac = await enrol_mac(clients(), marco, ctx, "marco@t.test")
    await admin.post(f"/devices/{mac.id}/revoke")
    assert await rotate_after_revocations(ctx) == 0  # revocations are still coming
    assert await rotate_after_revocations(ctx, quiet=timedelta(0)) == 1
    await run_rotations(ctx)
    key = (await admin.get("/key")).json()
    assert key["window"] is None
    assert key["rotation"] is None
    assert first_ref in fake.deleted
    async with owner_engine.connect() as conn:
        states = (
            (await conn.execute(select(S3Key.state).order_by(S3Key.created_at))).scalars().all()
        )
        org_row = (await conn.execute(select(Organization.storage_mode))).scalar_one()
    assert states == ["retired", "active"]
    assert org_row == "managed"
    logged = await actions(owner_engine)
    started = next(d for a, d in logged if a == "key_rotation_started")
    assert started["trigger"] == "revocation"
    assert ("key_window_closed", {"oldKeyTail": started["oldKeyTail"], "reasons": 1}) in logged
