"""The S3 key's rotation: its state machine, the worker, the window and the key states."""

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.disks.service import run_disk_formats
from floppy_server.infra.juicefs import FakeVolumeFormatter
from floppy_server.keys.service import run_rotations
from floppy_server.models import AuditEvent, KeyExposure, S3Key, Volume
from tests.conftest import ClientFactory, Org
from tests.helpers import enrol_mac, invite_and_join

NEW_KEY = {"accessKeyId": "AKIASECONDKEY00002", "secretKey": "second-secret-key-value-0002"}


def formatter(ctx: Context) -> FakeVolumeFormatter:
    assert isinstance(ctx.formatter, FakeVolumeFormatter)
    return ctx.formatter


async def make_disks(
    admin: httpx.AsyncClient, ctx: Context, *names: str
) -> list[dict[str, object]]:
    disks = []
    for name in names:
        response = await admin.post("/disks", json={"name": name.title(), "trashDays": 7})
        assert response.status_code == 201, response.text
        disks.append(response.json())
    await run_disk_formats(ctx)
    return disks


async def key_states(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(select(S3Key.access_key_tail, S3Key.state))).all()
    return {str(tail): str(state) for tail, state in rows}


async def test_key_status_after_bootstrap(org: Org, admin: httpx.AsyncClient) -> None:
    key = (await admin.get("/key")).json()
    assert key == {
        "bucket": org.bucket,
        "storageMode": "manual",
        "storageProvider": None,
        "keyScope": "team",
        "accessKeyTail": "00001",
        "rotatedAt": None,
        "window": None,
        "rotation": None,
        "keys": [],
        "versioning": {"state": "unknown", "oldVersionsDays": None},
    }


async def test_rotation_configures_every_disk_then_waits_for_the_old_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    await make_disks(admin, ctx, "archivio", "progetti")
    started = await admin.post("/key/rotations", json=NEW_KEY)
    assert started.status_code == 201
    rotation = started.json()
    assert rotation["state"] == "applying"
    assert (rotation["newKeyTail"], rotation["oldKeyTails"]) == ("00002", ["00001"])
    assert [d["state"] for d in rotation["disks"]] == ["pending", "pending"]
    assert rotation["probe"] == "pending"
    assert "secretKey" not in started.text and NEW_KEY["secretKey"] not in started.text

    await run_rotations(ctx)
    done = (await admin.get(f"/key/rotations/{rotation['id']}")).json()
    assert done["state"] == "awaiting_old_key_deletion"
    assert done["probe"] == "done"
    assert [d["state"] for d in done["disks"]] == ["done", "done"]
    assert all(
        f["access_key_id"] == NEW_KEY["accessKeyId"] for f in formatter(ctx).formats.values()
    )
    assert await key_states(owner_engine) == {"00001": "retiring", "00002": "active"}
    key = (await admin.get("/key")).json()
    assert key["accessKeyTail"] == "00002"
    assert key["rotatedAt"] is not None

    closed = await admin.post(
        f"/key/rotations/{rotation['id']}/close", json={"oldKeyDeleted": True}
    )
    assert closed.status_code == 200
    assert closed.json()["rotation"] is None
    assert await key_states(owner_engine) == {"00001": "retired", "00002": "active"}
    async with owner_engine.connect() as conn:
        wiped = (
            await conn.execute(select(S3Key.secret_key_enc).where(S3Key.state == "retired"))
        ).scalar_one()
        actions = (
            await conn.execute(select(AuditEvent.action, AuditEvent.detail).order_by(AuditEvent.id))
        ).all()
        on_new_key = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM volumes v JOIN s3_keys k ON k.id = v.s3_key_id WHERE k.state = 'active'"
                )
            )
        ).scalar_one()
    assert wiped is None
    assert on_new_key == 2
    assert ("key_rotation_started", {"newKeyTail": "00002", "oldKeyTail": "00001"}) in actions
    assert ("key_rotated", {"disks": 2}) in actions
    assert ("key_window_closed", {"oldKeyTail": "00001", "reasons": 0}) in actions


async def test_one_rotation_at_a_time(org: Org, admin: httpx.AsyncClient) -> None:
    assert (await admin.post("/key/rotations", json=NEW_KEY)).status_code == 201
    second = await admin.post(
        "/key/rotations",
        json={"accessKeyId": "AKIATHIRDKEY00003", "secretKey": "third-secret-0003"},
    )
    assert (second.status_code, second.json()["code"]) == (409, "rotation_in_progress")
    disk = await admin.post("/disks", json={"name": "Nuovo", "trashDays": 7})
    assert disk.json()["code"] == "rotation_in_progress"


async def test_the_key_in_use_cannot_be_rotated_to_itself(
    org: Org, admin: httpx.AsyncClient
) -> None:
    response = await admin.post(
        "/key/rotations", json={"accessKeyId": "AKIAFIRSTKEY00001", "secretKey": "whatever-secret"}
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation")


async def test_a_refused_probe_rejects_the_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    await make_disks(admin, ctx, "archivio")
    rotation = (
        await admin.post(
            "/key/rotations",
            json={"accessKeyId": "AKIADENIED0000004", "secretKey": "denied-secret-0004"},
        )
    ).json()
    await run_rotations(ctx)
    failed = (await admin.get(f"/key/rotations/{rotation['id']}")).json()
    assert failed["state"] == "failed"
    assert failed["probe"] == "failed"
    assert failed["error"] == {
        "step": "probe",
        "code": "AccessDenied",
        "request": f"PUT /{org.bucket}/floppy-probe-{rotation['id'][-4:]}",
    }
    assert [d["state"] for d in failed["disks"]] == ["pending"]
    assert await key_states(owner_engine) == {"00001": "active", "00004": "rejected"}
    key = (await admin.get("/key")).json()
    assert key["rotation"]["state"] == "failed"
    health = (await admin.get("/health")).json()
    assert health["storage"]["state"] == "failing"
    # A new rotation may start after a failed one.
    assert (await admin.post("/key/rotations", json=NEW_KEY)).status_code == 201


async def test_a_failed_disk_leaves_the_key_pending_and_a_retry_finishes(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    await make_disks(admin, ctx, "archivio", "progetti")
    formatter(ctx).fail_config.add("progetti")
    first = (await admin.post("/key/rotations", json=NEW_KEY)).json()
    await run_rotations(ctx)
    failed = (await admin.get(f"/key/rotations/{first['id']}")).json()
    assert failed["state"] == "failed"
    assert [d["state"] for d in failed["disks"]] == ["done", "failed"]
    assert (failed["error"]["step"], failed["error"]["code"]) == ("disk", "JuiceFSConfigFailed")
    assert await key_states(owner_engine) == {"00001": "active", "00002": "pending"}

    formatter(ctx).fail_config.clear()
    retry = (await admin.post("/key/rotations", json=NEW_KEY)).json()
    await run_rotations(ctx)
    assert (await admin.get(f"/key/rotations/{retry['id']}")).json()[
        "state"
    ] == "awaiting_old_key_deletion"
    async with owner_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(S3Key.access_key_tail).where(
                        S3Key.access_key_id == NEW_KEY["accessKeyId"]
                    )
                )
            )
            .scalars()
            .all()
        )
        keys = set((await conn.execute(select(Volume.s3_key_id))).scalars())
    assert rows == ["00002"]
    assert len(keys) == 1


async def test_the_window_closes_only_for_exposures_before_the_rotation(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    await enrol_mac(clients(), marco, ctx, "marco@studio-a.test", name="iMac di Marco")
    late_mac = await enrol_mac(clients(), admin, ctx, org.admin_email, name="Portatile")
    removed = (await admin.delete(f"/people/{person['id']}")).json()
    assert removed["key"]["window"]["reasons"][0]["kind"] == "person_removed"

    rotation = (await admin.post("/key/rotations", json=NEW_KEY)).json()
    # Revoked while the rotation applies: it may already have read the new key.
    await admin.post(f"/devices/{late_mac.id}/revoke")
    await run_rotations(ctx)
    closed = (
        await admin.post(f"/key/rotations/{rotation['id']}/close", json={"oldKeyDeleted": True})
    ).json()
    assert [r["label"] for r in closed["window"]["reasons"]] == ["Portatile"]
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
    first = (await admin.post("/key/rotations", json=NEW_KEY)).json()
    await run_rotations(ctx)
    second = (
        await admin.post(
            "/key/rotations",
            json={"accessKeyId": "AKIATHIRDKEY00003", "secretKey": "third-secret-0003"},
        )
    ).json()
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


async def test_rotation_counts_macs_that_picked_up_the_new_key(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email)
    beat = {"appVersion": "0.4.0", "macosVersion": "15.5", "s3AccessKeyTail": "00001"}
    await app.post("/device/heartbeat", json=beat, headers=mac.auth)
    rotation = (await admin.post("/key/rotations", json=NEW_KEY)).json()
    assert rotation["macs"] == {"updated": 0, "total": 1}
    await run_rotations(ctx)
    await app.post("/device/heartbeat", json={**beat, "s3AccessKeyTail": "00002"}, headers=mac.auth)
    assert (await admin.get(f"/key/rotations/{rotation['id']}")).json()["macs"] == {
        "updated": 1,
        "total": 1,
    }


async def test_close_needs_the_confirmation(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    rotation = (await admin.post("/key/rotations", json=NEW_KEY)).json()
    await run_rotations(ctx)
    response = await admin.post(
        f"/key/rotations/{rotation['id']}/close", json={"oldKeyDeleted": False}
    )
    assert (response.status_code, response.json()["code"]) == (422, "validation")


async def test_a_short_key_is_a_validation_error_without_echo(
    org: Org, admin: httpx.AsyncClient
) -> None:
    response = await admin.post("/key/rotations", json={"accessKeyId": "AKIA", "secretKey": "s3cr"})
    assert (response.status_code, response.json()["code"]) == (422, "validation")
    assert "s3cr" not in response.text
