"""
Access per person and per disk (DATABASE_SCHEMA.md, "Access per disk"): the defaults, the
matrix, what a device is given, live changes on the instances, the key window in manual mode,
and work owed while an instance is down. Instances are the labelled `FakeMetadataAdmin`; the
real Valkey refusing writes is in test_perdisk.py.
"""

import hashlib
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.background import reconcile_instances
from floppy_server.context import Context
from floppy_server.models import AuditEvent, KeyExposure, MetaUser
from tests.conftest import ClientFactory, Org
from tests.helpers import (
    create_disk,
    enrol_device,
    fake_valkey,
    instance_url,
    invite_and_join,
    set_access,
)

BEAT = {"appVersion": "0.4.0", "macosVersion": "15.5"}


async def person_id(admin: httpx.AsyncClient, email: str) -> str:
    people = (await admin.get("/people")).json()
    found: str = next(p["id"] for p in people if p["email"] == email)
    return found


def matrix(entries: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    return {(e["diskId"], e["personId"]): e["access"] for e in entries}


async def test_defaults_creator_writes_others_see_nothing(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    marco = await invite_and_join(admin, clients(), ctx, "marco@studio-a.test")
    disk = await create_disk(admin, ctx, "Progetti")
    giulia = await person_id(admin, org.admin_email)
    entries = (await admin.get("/access")).json()
    assert matrix(entries) == {(disk["id"], giulia): "write", (disk["id"], marco["id"]): "none"}
    assert all(e["applied"] for e in entries)
    # A person invited later sees no disk unless the invitation lists it.
    await admin.post("/people", json={"email": "sara@studio-a.test", "role": "member"})
    await admin.post(
        "/people",
        json={
            "email": "luca@studio-a.test",
            "role": "member",
            "disks": [{"diskId": disk["id"], "access": "read"}],
        },
    )
    people = {p["email"]: p["id"] for p in (await admin.get("/people")).json()}
    got = matrix((await admin.get("/access")).json())
    assert got[(disk["id"], people["sara@studio-a.test"])] == "none"
    assert got[(disk["id"], people["luca@studio-a.test"])] == "read"


async def test_creation_takes_the_listed_access(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    marco = await invite_and_join(admin, clients(), ctx, "marco@studio-a.test")
    giulia = await person_id(admin, org.admin_email)
    disk = await create_disk(
        admin,
        ctx,
        "Archivio",
        access=[
            {"personId": marco["id"], "access": "read"},
            {"personId": giulia, "access": "none"},
        ],
    )
    got = matrix((await admin.get("/access")).json())
    assert got == {(disk["id"], giulia): "none", (disk["id"], marco["id"]): "read"}
    unknown = await admin.post(
        "/disks",
        json={
            "name": "Altro",
            "storage": {
                "bucket": "altro-bucket",
                "accessKeyId": "AKIAALTRO00001",
                "secretKey": "x" * 12,
            },
            "access": [{"personId": disk["id"], "access": "read"}],
        },
    )
    assert (unknown.status_code, unknown.json()["code"]) == (422, "validation")


async def test_a_device_gets_users_only_on_the_disks_it_may_see(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    progetti = await create_disk(admin, ctx, "Progetti")
    archivio = await create_disk(admin, ctx, "Archivio")
    riservato = await create_disk(admin, ctx, "Riservato")
    giulia = await person_id(admin, org.admin_email)
    await set_access(admin, archivio["id"], giulia, "read")
    await set_access(admin, riservato["id"], giulia, "none")
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    assert device.valkey_password
    digest = hashlib.sha256(device.valkey_password.encode()).digest()
    assert [(d["name"], d["access"]) for d in device.disks] == [
        ("Progetti", "write"),
        ("Archivio", "read"),
    ]
    assert device.disks[0]["metaUrl"] == f"{instance_url(ctx, progetti['id'])}/1"
    valkey = fake_valkey(ctx)
    assert valkey.users[instance_url(ctx, progetti["id"])][device.valkey_user] == (digest, "write")
    assert valkey.users[instance_url(ctx, archivio["id"])][device.valkey_user] == (digest, "read")
    assert device.valkey_user not in valkey.users.get(instance_url(ctx, riservato["id"]), {})
    config = (await app.get("/device/config", headers=device.auth)).json()
    assert config["disks"] == device.disks
    assert config["storageMode"] == "manual"
    assert config["s3AccessKeyTail"] is None
    assert device.s3 is None  # manual: the key is in each disk's format
    beat = (await app.post("/device/heartbeat", json=BEAT, headers=device.auth)).json()
    assert beat == {
        "state": "active",
        "disks": [
            {"id": progetti["id"], "access": "write"},
            {"id": archivio["id"], "access": "read"},
        ],
    }


async def test_changes_apply_live_and_lowering_to_read_tells_the_device(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    disk = await create_disk(admin, ctx, "Progetti")
    marco = await invite_and_join(admin, clients(), ctx, "marco@studio-a.test")
    app = clients()
    member = clients()
    await invite_and_join(admin, member, ctx, "sara@studio-a.test")
    device = await enrol_device(app, member, ctx, "sara@studio-a.test", name="iMac di Sara")
    sara = await person_id(admin, "sara@studio-a.test")
    url = instance_url(ctx, disk["id"])
    valkey = fake_valkey(ctx)
    assert device.disks == []
    assert valkey.access(url, device.valkey_user) is None

    raised = await set_access(admin, disk["id"], sara, "write")
    assert raised == {"diskId": disk["id"], "personId": sara, "access": "write", "applied": True}
    assert valkey.access(url, device.valkey_user) == "write"
    beat = (await app.post("/device/heartbeat", json=BEAT, headers=device.auth)).json()
    assert beat["disks"] == [{"id": disk["id"], "access": "write"}]

    # Lowered to read: the ACL changes in place, open connections are not closed, and the
    # heartbeat lists the disk as read so the app remounts it read-only.
    valkey.killed.clear()
    await set_access(admin, disk["id"], sara, "read")
    assert valkey.access(url, device.valkey_user) == "read"
    assert valkey.killed == []
    beat = (await app.post("/device/heartbeat", json=BEAT, headers=device.auth)).json()
    assert beat["disks"] == [{"id": disk["id"], "access": "read"}]

    # To none: the user is gone and the disk is no longer listed.
    await set_access(admin, disk["id"], sara, "none")
    assert valkey.access(url, device.valkey_user) is None
    assert device.valkey_user in valkey.killed
    beat = (await app.post("/device/heartbeat", json=BEAT, headers=device.auth)).json()
    assert beat["disks"] == []
    # Setting the same access again is a no-op; a member cannot set any.
    await set_access(admin, disk["id"], sara, "none")
    forbidden = await member.put(
        f"/disks/{disk['id']}/access/{marco['id']}", json={"access": "write"}
    )
    assert forbidden.status_code == 403
    async with owner_engine.connect() as conn:
        changes = (
            (
                await conn.execute(
                    select(AuditEvent.detail)
                    .where(AuditEvent.action == "disk_access_changed")
                    .order_by(AuditEvent.id)
                )
            )
            .scalars()
            .all()
        )
    assert changes[-3:] == [
        {"person": "Sara", "from": "none", "to": "write"},
        {"person": "Sara", "from": "write", "to": "read"},
        {"person": "Sara", "from": "read", "to": "none"},
    ]


async def test_manual_lowering_to_none_opens_that_disks_window_only(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    progetti = await create_disk(admin, ctx, "Progetti")
    archivio = await create_disk(admin, ctx, "Archivio")
    member = clients()
    await invite_and_join(admin, member, ctx, "sara@studio-a.test")
    sara = await person_id(admin, "sara@studio-a.test")
    nobody = clients()
    await invite_and_join(admin, nobody, ctx, "luca@studio-a.test")
    luca = await person_id(admin, "luca@studio-a.test")
    for disk in (progetti, archivio):
        await set_access(admin, disk["id"], sara, "write")
        await set_access(admin, disk["id"], luca, "read")
    await enrol_device(clients(), member, ctx, "sara@studio-a.test", name="iMac di Sara")
    # Lowered to read: Sara still sees the disk, nothing to rotate.
    await set_access(admin, progetti["id"], sara, "read")
    # Luca never had a device: lowering him opens nothing.
    await set_access(admin, progetti["id"], luca, "none")
    await set_access(admin, progetti["id"], sara, "none")
    key = (await admin.get("/key")).json()
    windows = {d["diskName"]: d["window"] for d in key["disks"]}
    assert windows["Archivio"] is None
    assert windows["Progetti"] is not None
    assert [(r["kind"], r["label"]) for r in windows["Progetti"]["reasons"]] == [
        ("access_removed", "Sara")
    ]
    async with owner_engine.connect() as conn:
        assert (await conn.execute(select(KeyExposure.kind))).scalars().all() == ["access_removed"]


async def test_work_owed_while_an_instance_is_down_lands_with_the_reconciler(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    disk = await create_disk(admin, ctx, "Progetti")
    member = clients()
    await invite_and_join(admin, member, ctx, "sara@studio-a.test")
    sara = await person_id(admin, "sara@studio-a.test")
    device = await enrol_device(clients(), member, ctx, "sara@studio-a.test", name="iMac di Sara")
    url = instance_url(ctx, disk["id"])
    valkey = fake_valkey(ctx)
    valkey.down.add(url)
    pending = await set_access(admin, disk["id"], sara, "write")
    assert pending["applied"] is False
    assert [e["applied"] for e in (await admin.get("/access")).json() if e["personId"] == sara] == [
        False
    ]
    async with owner_engine.connect() as conn:
        owed = (await conn.execute(select(MetaUser.access, MetaUser.applied_at))).one()
    assert tuple(owed) == ("write", None)
    valkey.down.clear()
    await reconcile_instances(ctx, org.id)
    assert valkey.access(url, device.valkey_user) == "write"
    assert all(e["applied"] for e in (await admin.get("/access")).json())


async def test_the_reconciler_rebuilds_a_restored_instance_and_drops_strays(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    progetti = await create_disk(admin, ctx, "Progetti")
    archivio = await create_disk(admin, ctx, "Archivio")
    giulia = await person_id(admin, org.admin_email)
    await set_access(admin, archivio["id"], giulia, "read")
    device = await enrol_device(clients(), admin, ctx, org.admin_email)
    valkey = fake_valkey(ctx)
    url_p, url_a = instance_url(ctx, progetti["id"]), instance_url(ctx, archivio["id"])
    expected_p = valkey.users[url_p][device.valkey_user]
    # A master rebuilt from a dump: ACL users are not replicated. A stray user shows up, and
    # one user was widened behind the control plane's back.
    valkey.users[url_p] = {"floppy_d_" + "f" * 32: (b"x" * 32, "write")}
    digest = valkey.users[url_a][device.valkey_user][0]
    valkey.users[url_a][device.valkey_user] = (digest, "write")
    await reconcile_instances(ctx, org.id)
    assert valkey.users[url_p] == {device.valkey_user: expected_p}
    assert valkey.users[url_a] == {device.valkey_user: (digest, "read")}


async def test_admins_list_access_members_do_not(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    member = clients()
    await invite_and_join(admin, member, ctx, "sara@studio-a.test")
    assert (await member.get("/access")).status_code == 403
    disk = await create_disk(admin, ctx, "Progetti")
    missing = await admin.put(f"/disks/{disk['id']}/access/{disk['id']}", json={"access": "read"})
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")
    bad = await admin.put(
        f"/disks/{disk['id']}/access/{await person_id(admin, org.admin_email)}",
        json={"access": "admin"},
    )
    assert bad.status_code == 422


async def test_my_disks_lists_only_what_the_person_sees(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    member = clients()
    marco = await invite_and_join(admin, member, ctx, "marco@studio-a.test")
    progetti = await create_disk(admin, ctx, "Progetti")
    archivio = await create_disk(admin, ctx, "Archivio")
    await create_disk(admin, ctx, "Nascosto")
    await set_access(admin, progetti["id"], marco["id"], "read")
    await set_access(admin, archivio["id"], marco["id"], "write")
    response = await member.get("/access/mine")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"id": progetti["id"], "name": "Progetti", "access": "read"},
        {"id": archivio["id"], "name": "Archivio", "access": "write"},
    ]
    # Lowered to none, the disk leaves the list; the admin sees the disks they created.
    await set_access(admin, progetti["id"], marco["id"], "none")
    assert [d["name"] for d in (await member.get("/access/mine")).json()] == ["Archivio"]
    assert [d["name"] for d in (await admin.get("/access/mine")).json()] == [
        "Progetti",
        "Archivio",
        "Nascosto",
    ]
    assert (await clients().get("/access/mine")).status_code == 401
