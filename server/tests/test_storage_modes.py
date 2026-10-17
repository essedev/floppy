"""
Storage modes (DATABASE_SCHEMA.md, "Storage modes"), the mail language and
`organization_created`. Managed mode with the labelled `FakeAdapter`: a bucket per disk owned
by the control plane's key, a key per device granted per bucket and changed in place, deleted
when the device goes. The real Garage is in test_garage.py and test_perdisk.py.
"""

from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.background import reconcile_instances
from floppy_server.context import Context
from floppy_server.disks.service import managed_bucket
from floppy_server.keys.managed import delete_owed_keys
from floppy_server.models import AuditEvent, S3Key
from tests.conftest import ClientFactory, Org, make_org
from tests.helpers import (
    create_disk,
    enrol_device,
    fake_adapter,
    formatter,
    instance_url,
    invite_and_join,
    mailer,
    set_access,
    sign_in,
)

BEAT = {"appVersion": "0.4.0", "macosVersion": "15.5"}


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


async def test_a_manual_organization_starts_with_no_key(
    org: Org, owner_engine: AsyncEngine
) -> None:
    assert await keys(owner_engine) == []


# Managed: a key per device, granted per bucket


@pytest.fixture
async def managed_org(ctx: Context) -> Org:
    fake_adapter(ctx)
    return await make_org(
        ctx, "studio-m", "mara@m.test", storage_mode="managed", storage_provider="garage"
    )


@pytest.fixture
async def managed_admin(
    managed_org: Org, ctx: Context, clients: ClientFactory
) -> httpx.AsyncClient:
    client = clients()
    await sign_in(client, ctx, managed_org.admin_email)
    return client


async def person(admin: httpx.AsyncClient, email: str) -> str:
    found: str = next(p["id"] for p in (await admin.get("/people")).json() if p["email"] == email)
    return found


async def test_managed_bootstrap_creates_the_control_planes_key(
    managed_org: Org, ctx: Context, owner_engine: AsyncEngine
) -> None:
    fake = fake_adapter(ctx)
    assert fake.buckets == set()
    async with owner_engine.connect() as conn:
        holder, origin, ref = (
            await conn.execute(select(S3Key.holder, S3Key.origin, S3Key.provider_ref))
        ).one()
    assert (holder, origin) == ("control_plane", "provider")
    assert ref in fake.keys


async def test_a_managed_disk_gets_its_bucket_owned_by_the_control_plane_and_no_key_inside(
    managed_org: Org, ctx: Context, managed_admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    fake = fake_adapter(ctx)
    pasted = await managed_admin.post(
        "/disks",
        json={
            "name": "Progetti",
            "storage": {"bucket": "mine", "accessKeyId": "AKIAMINE0001", "secretKey": "x" * 12},
        },
    )
    assert pasted.json()["details"] == {"field": "storage"}
    disk = await create_disk(managed_admin, ctx, "Progetti", managed=True)
    assert disk["state"] == "ready"
    bucket = managed_bucket(UUID(disk["id"]))
    assert disk["bucket"] == bucket
    assert bucket in fake.buckets
    async with owner_engine.connect() as conn:
        cp_ref = (await conn.execute(select(S3Key.provider_ref))).scalar_one()
    assert cp_ref is not None
    assert fake.grants[(cp_ref, bucket)] == ("write", True)
    assert formatter(ctx).formats[(instance_url(ctx, disk["id"]), 1)]["access_key_id"] == ""
    assert (
        await managed_admin.post(
            "/settings/storage/probe",
            json={
                "endpoint": "https://s3.test",
                "bucket": "x-bucket",
                "accessKeyId": "AKIAPROBE0001",
                "secretKey": "y" * 12,
            },
        )
    ).json()["code"] == "invalid_state"


async def test_each_device_gets_its_own_key_granted_per_bucket_and_changed_in_place(
    managed_org: Org,
    ctx: Context,
    managed_admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    fake = fake_adapter(ctx)
    marco = clients()
    await invite_and_join(managed_admin, marco, ctx, "marco@m.test")
    marco_id = await person(managed_admin, "marco@m.test")
    progetti = await create_disk(managed_admin, ctx, "Progetti", managed=True)
    archivio = await create_disk(managed_admin, ctx, "Archivio", managed=True)
    riservato = await create_disk(managed_admin, ctx, "Riservato", managed=True)
    await set_access(managed_admin, progetti["id"], marco_id, "write")
    await set_access(managed_admin, archivio["id"], marco_id, "read")
    app = clients()
    device = await enrol_device(app, marco, ctx, "marco@m.test", name="iMac di Marco")
    assert device.s3 is not None
    ref = device.s3["accessKeyId"]
    assert ref in fake.keys
    assert [(d["name"], d["access"]) for d in device.disks] == [
        ("Progetti", "write"),
        ("Archivio", "read"),
    ]
    assert fake.grant(ref, progetti["bucket"]) == "write"
    assert fake.grant(ref, archivio["bucket"]) == "read"
    assert fake.grant(ref, riservato["bucket"]) == "none"
    config = (await app.get("/device/config", headers=device.auth)).json()
    assert (config["storageMode"], config["s3AccessKeyTail"]) == ("managed", ref[-5:])
    async with owner_engine.connect() as conn:
        secret = (
            await conn.execute(select(S3Key.secret_key_enc).where(S3Key.holder == "device"))
        ).scalar_one()
    assert secret is None  # a device's own secret is returned once, never kept

    # Changed in place: the same key, another grant; to none, no grant at all.
    await set_access(managed_admin, progetti["id"], marco_id, "read")
    await set_access(managed_admin, archivio["id"], marco_id, "none")
    assert (fake.grant(ref, progetti["bucket"]), fake.grant(ref, archivio["bucket"])) == (
        "read",
        "none",
    )
    assert ref in fake.keys
    # No window in managed mode.
    status = (await managed_admin.get("/key")).json()
    assert all(d["window"] is None and d["accessKeyTail"] is None for d in status["disks"])
    assert [k["state"] for k in status["keys"]] == ["active"]
    assert status["controlPlaneKeyTail"] is not None


async def test_a_reissue_replaces_the_key_and_a_revocation_deletes_it(
    managed_org: Org,
    ctx: Context,
    managed_admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    fake = fake_adapter(ctx)
    disk = await create_disk(managed_admin, ctx, "Progetti", managed=True)
    app = clients()
    device = await enrol_device(app, managed_admin, ctx, managed_org.admin_email)
    assert device.s3 is not None
    again = (await app.post("/device/register", json=BEAT, headers=device.auth)).json()
    new_ref = again["s3"]["accessKeyId"]
    assert new_ref != device.s3["accessKeyId"]
    assert device.s3["accessKeyId"] in fake.deleted
    assert fake.grant(new_ref, disk["bucket"]) == "write"

    fake.down = True
    revoked = (await managed_admin.post(f"/devices/{device.id}/revoke")).json()
    assert revoked["key"]["disks"][0]["window"] is None
    assert [k["state"] for k in revoked["key"]["keys"]] == ["retiring"]
    fake.down = False
    assert await delete_owed_keys(ctx, managed_org.id) == 1
    assert new_ref in fake.deleted
    assert (fake.grant(new_ref, disk["bucket"])) == "none"
    stored = [k for k in await keys(owner_engine) if k[0] == "device"]
    assert [k[2] for k in stored] == ["retired", "retired"]
    logged = [d for a, d in await actions(owner_engine) if a == "key_deleted"]
    assert [d["by"] for d in logged] == ["adapter", "adapter"]
    rotation = await managed_admin.post(
        "/key/rotations",
        json={"diskId": disk["id"], "accessKeyId": "AKIAROTATE0001", "secretKey": "z" * 12},
    )
    assert rotation.json()["code"] == "invalid_state"


async def test_a_grant_the_provider_did_not_take_is_owed(
    managed_org: Org, ctx: Context, managed_admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    fake = fake_adapter(ctx)
    disk = await create_disk(managed_admin, ctx, "Progetti", managed=True)
    marco = clients()
    await invite_and_join(managed_admin, marco, ctx, "marco@m.test")
    marco_id = await person(managed_admin, "marco@m.test")
    device = await enrol_device(clients(), marco, ctx, "marco@m.test", name="iMac di Marco")
    assert device.s3 is not None
    fake.down = True
    owed = await set_access(managed_admin, disk["id"], marco_id, "read")
    assert owed["applied"] is False
    fake.down = False
    await reconcile_instances(ctx, managed_org.id)
    assert fake.grant(device.s3["accessKeyId"], disk["bucket"]) == "read"
    assert all(e["applied"] for e in (await managed_admin.get("/access")).json())


async def test_the_schema_holds_the_holders(managed_org: Org, owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_s3_keys_device_holder"):
            await conn.execute(text("UPDATE s3_keys SET holder = 'device'"))
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_s3_keys_disk_holder"):
            await conn.execute(text("UPDATE s3_keys SET holder = 'disk'"))
