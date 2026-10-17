"""
A manual disk's key and its rotation: the state machine, the worker, the window per disk and
the key states. JuiceFS and the probe are the labelled fakes; the real `juicefs config` with a
key from the environment is in test_juicefs_s3.py.
"""

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.keys.service import run_rotations
from floppy_server.models import AuditEvent, KeyExposure, S3Key
from tests.conftest import ClientFactory, Org
from tests.helpers import (
    create_disk,
    disk_key,
    enrol_device,
    formatter,
    instance_url,
    invite_and_join,
)


def new_key(n: int, disk: dict[str, Any]) -> dict[str, Any]:
    return {"diskId": disk["id"], **disk_key(n)}


async def key_states(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(select(S3Key.access_key_tail, S3Key.state))).all()
    return {str(tail): str(state) for tail, state in rows}


async def test_key_status_lists_each_disk(org: Org, ctx: Context, admin: httpx.AsyncClient) -> None:
    empty = (await admin.get("/key")).json()
    assert empty == {
        "storageMode": "manual",
        "storageProvider": None,
        "controlPlaneKeyTail": None,
        "disks": [],
        "keys": [],
    }
    disk = await create_disk(admin, ctx, "Archivio", bucket="studio-archivio", key=disk_key(1))
    key = (await admin.get("/key")).json()
    assert key["disks"] == [
        {
            "diskId": disk["id"],
            "diskName": "Archivio",
            "bucket": "studio-archivio",
            "endpoint": "https://s3.test",
            "accessKeyTail": "00001",
            "rotatedAt": None,
            "window": None,
            "rotation": None,
            "versioning": {"state": "unknown", "oldVersionsDays": None},
        }
    ]


async def test_rotation_configures_its_disk_only_then_waits_for_the_old_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    archivio = await create_disk(admin, ctx, "Archivio", key=disk_key(1))
    progetti = await create_disk(admin, ctx, "Progetti", key=disk_key(9))
    response = await admin.post("/key/rotations", json=new_key(2, archivio))
    assert response.status_code == 201, response.text
    rotation = response.json()
    assert (rotation["state"], rotation["diskName"], rotation["probe"], rotation["config"]) == (
        "applying",
        "Archivio",
        "pending",
        "pending",
    )
    assert (rotation["newKeyTail"], rotation["oldKeyTails"]) == ("00002", ["00001"])
    assert await run_rotations(ctx) == 3  # probe, config, switch
    done = (await admin.get(f"/key/rotations/{rotation['id']}")).json()
    assert (done["state"], done["probe"], done["config"]) == (
        "awaiting_old_key_deletion",
        "done",
        "done",
    )
    formats = formatter(ctx).formats
    assert formats[(instance_url(ctx, archivio["id"]), 1)]["access_key_id"] == "AKIATESTDISK00002"
    assert formats[(instance_url(ctx, progetti["id"]), 1)]["access_key_id"] == "AKIATESTDISK00009"
    assert await key_states(owner_engine) == {
        "00001": "retiring",
        "00002": "active",
        "00009": "active",
    }
    closed = await admin.post(
        f"/key/rotations/{rotation['id']}/close", json={"oldKeyDeleted": True}
    )
    assert closed.status_code == 200
    disks = {d["diskName"]: d for d in closed.json()["disks"]}
    assert disks["Archivio"]["accessKeyTail"] == "00002"
    assert disks["Archivio"]["rotatedAt"] is not None
    assert disks["Archivio"]["rotation"] is None
    assert await key_states(owner_engine) == {
        "00001": "retired",
        "00002": "active",
        "00009": "active",
    }
    async with owner_engine.connect() as conn:
        rows = (await conn.execute(select(AuditEvent.action, AuditEvent.detail))).all()
    details = {row[0]: row[1] for row in rows}
    assert details["key_rotation_started"] == {
        "disk": "Archivio",
        "newKeyTail": "00002",
        "oldKeyTail": "00001",
    }
    assert details["key_rotated"] == {"disk": "Archivio"}
    assert details["key_window_closed"] == {"disk": "Archivio", "oldKeyTail": "00001", "reasons": 0}


async def test_one_rotation_per_disk_at_a_time(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    archivio = await create_disk(admin, ctx, "Archivio")
    progetti = await create_disk(admin, ctx, "Progetti")
    assert (await admin.post("/key/rotations", json=new_key(2, archivio))).status_code == 201
    busy = await admin.post("/key/rotations", json=new_key(3, archivio))
    assert (busy.status_code, busy.json()["code"]) == (409, "rotation_in_progress")
    # Another disk rotates on its own.
    assert (await admin.post("/key/rotations", json=new_key(4, progetti))).status_code == 201


async def test_the_key_in_use_cannot_be_rotated_to_itself_nor_to_another_disks(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    archivio = await create_disk(admin, ctx, "Archivio", key=disk_key(1))
    await create_disk(admin, ctx, "Progetti", key=disk_key(5))
    same = await admin.post("/key/rotations", json=new_key(1, archivio))
    assert (same.status_code, same.json()["code"]) == (422, "validation")
    other = await admin.post("/key/rotations", json=new_key(5, archivio))
    assert (other.status_code, other.json()["code"]) == (422, "validation")


async def test_a_refused_probe_rejects_the_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    disk = await create_disk(admin, ctx, "Archivio", key=disk_key(1))
    body = {"diskId": disk["id"], "accessKeyId": "AKIADENIEDKEY0007", "secretKey": "x" * 16}
    rotation = (await admin.post("/key/rotations", json=body)).json()
    await run_rotations(ctx)
    failed = (await admin.get(f"/key/rotations/{rotation['id']}")).json()
    assert (failed["state"], failed["probe"]) == ("failed", "failed")
    assert failed["error"]["step"] == "probe"
    assert failed["error"]["code"] == "AccessDenied"
    assert (await key_states(owner_engine))["Y0007"] == "rejected"
    health = (await admin.get("/health")).json()
    assert health["disks"][0]["storage"]["state"] == "failing"


async def test_a_failed_config_leaves_the_key_pending_and_a_retry_finishes(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    disk = await create_disk(admin, ctx, "Archivio", key=disk_key(1))
    formatter(ctx).fail_config.add("archivio")
    rotation = (await admin.post("/key/rotations", json=new_key(2, disk))).json()
    await run_rotations(ctx)
    failed = (await admin.get(f"/key/rotations/{rotation['id']}")).json()
    assert (failed["state"], failed["config"], failed["error"]["step"]) == (
        "failed",
        "failed",
        "config",
    )
    assert await key_states(owner_engine) == {"00001": "active", "00002": "pending"}
    formatter(ctx).fail_config.clear()
    retry = (await admin.post("/key/rotations", json=new_key(2, disk))).json()
    await run_rotations(ctx)
    assert (await admin.get(f"/key/rotations/{retry['id']}")).json()["state"] == (
        "awaiting_old_key_deletion"
    )
    assert await key_states(owner_engine) == {"00001": "retiring", "00002": "active"}


async def test_the_window_closes_only_for_exposures_before_the_rotation(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    disk = await create_disk(
        admin,
        ctx,
        "Archivio",
        key=disk_key(1),
        access=[{"personId": person["id"], "access": "read"}],
    )
    await enrol_device(clients(), marco, ctx, "marco@studio-a.test", name="iMac di Marco")
    late = await enrol_device(clients(), admin, ctx, org.admin_email, name="Portatile")
    removed = (await admin.delete(f"/people/{person['id']}")).json()
    window = removed["key"]["disks"][0]["window"]
    assert [(r["kind"], r["label"]) for r in window["reasons"]] == [("person_removed", "Marco")]

    rotation = (await admin.post("/key/rotations", json=new_key(2, disk))).json()
    # Revoked while the rotation applies: it may already have read the new key.
    await admin.post(f"/devices/{late.id}/revoke")
    await run_rotations(ctx)
    closed = (
        await admin.post(f"/key/rotations/{rotation['id']}/close", json={"oldKeyDeleted": True})
    ).json()
    assert [r["label"] for r in closed["disks"][0]["window"]["reasons"]] == ["Portatile"]
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(select(KeyExposure.kind, KeyExposure.closed_by_rotation_id))
        ).all()
    assert sorted((k, c is not None) for k, c in rows) == [
        ("device_revoked", False),
        ("person_removed", True),
    ]


async def test_a_new_rotation_supersedes_one_awaiting_deletion(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    disk = await create_disk(admin, ctx, "Archivio", key=disk_key(1))
    first = (await admin.post("/key/rotations", json=new_key(2, disk))).json()
    await run_rotations(ctx)
    second = (await admin.post("/key/rotations", json=new_key(3, disk))).json()
    assert second["oldKeyTails"] == ["00001", "00002"]
    await run_rotations(ctx)
    stale = await admin.post(f"/key/rotations/{first['id']}/close", json={"oldKeyDeleted": True})
    assert (stale.status_code, stale.json()["code"]) == (409, "invalid_state")
    assert await key_states(owner_engine) == {
        "00001": "retiring",
        "00002": "retiring",
        "00003": "active",
    }
    await admin.post(f"/key/rotations/{second['id']}/close", json={"oldKeyDeleted": True})
    assert await key_states(owner_engine) == {
        "00001": "retired",
        "00002": "retired",
        "00003": "active",
    }


async def test_rotation_counts_devices_seeing_the_disk_that_picked_up_the_new_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk = await create_disk(admin, ctx, "Archivio", key=disk_key(1))
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    beat = {"appVersion": "0.4.0", "macosVersion": "15.5", "s3AccessKeyTail": "00001"}
    await app.post("/device/heartbeat", json=beat, headers=device.auth)
    rotation = (await admin.post("/key/rotations", json=new_key(2, disk))).json()
    assert rotation["devices"] == {"updated": 0, "total": 1}
    await run_rotations(ctx)
    await app.post(
        "/device/heartbeat", json={**beat, "s3AccessKeyTail": "00002"}, headers=device.auth
    )
    assert (await admin.get(f"/key/rotations/{rotation['id']}")).json()["devices"] == {
        "updated": 1,
        "total": 1,
    }


async def test_close_needs_the_confirmation(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    disk = await create_disk(admin, ctx, "Archivio")
    rotation = (await admin.post("/key/rotations", json=new_key(2, disk))).json()
    await run_rotations(ctx)
    response = await admin.post(
        f"/key/rotations/{rotation['id']}/close", json={"oldKeyDeleted": False}
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation")


async def test_a_short_key_is_a_validation_error_without_echo(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    disk = await create_disk(admin, ctx, "Archivio")
    response = await admin.post(
        "/key/rotations", json={"diskId": disk["id"], "accessKeyId": "AKIA", "secretKey": "s3cr"}
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation")
    assert "s3cr" not in response.text
