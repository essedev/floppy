"""Disks: creation and the format job, names, the database limit, updates, counters."""

from typing import Any

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.background import expire_stale_rows, poll_status
from floppy_server.context import Context
from floppy_server.disks.service import derive_volume_name, run_disk_formats
from floppy_server.infra.juicefs import FakeVolumeFormatter
from floppy_server.infra.metadata import FakeMetadataAdmin, VolumeCounters
from floppy_server.models import AuditEvent, Organization, Volume
from tests.conftest import ClientFactory, Org
from tests.helpers import enrol_mac


def formatter(ctx: Context) -> FakeVolumeFormatter:
    assert isinstance(ctx.formatter, FakeVolumeFormatter)
    return ctx.formatter


async def create(admin: httpx.AsyncClient, ctx: Context, name: str, **extra: Any) -> dict[str, Any]:
    """Creates a disk and runs the format job; returns it as GET /disks/{id} sees it."""
    response = await admin.post("/disks", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    await run_disk_formats(ctx)
    disk: dict[str, Any] = (await admin.get(f"/disks/{response.json()['id']}")).json()
    return disk


async def volume_names(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(Volume.volume_name).order_by(Volume.created_at))
        return [str(name) for name in rows.scalars()]


def test_volume_names_derive_from_the_display_name() -> None:
    assert derive_volume_name("Archivio 2025", set()) == "archivio-2025"
    assert derive_volume_name("Progetti", {"progetti"}) == "progetti-2"
    assert derive_volume_name("Progetti", {"progetti", "progetti-2"}) == "progetti-3"
    assert derive_volume_name("A", set()) == "a-2"
    assert derive_volume_name("Clip_finali.v2", set()) == "clip-finali-v2"
    assert len(derive_volume_name("X" * 60, set())) == 58


async def test_create_answers_creating_then_the_job_formats_it(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    response = await admin.post("/disks", json={"name": "Archivio 2025", "trashDays": 14})
    assert response.status_code == 201
    created = response.json()
    assert created["state"] == "creating"
    assert created["mountPath"] == "/Volumes/Floppy/Archivio 2025"
    assert "volumeName" not in created
    assert "prefix" not in created
    assert created["error"] is None
    assert await run_disk_formats(ctx) == 1
    disk = (await admin.get(f"/disks/{created['id']}")).json()
    assert disk["state"] == "ready"
    assert disk["metaDb"] == 1
    assert disk["blockSizeKb"] == 1024
    assert disk["trashDays"] == 14
    assert disk["cache"] == {"expireDays": 30}
    assert disk["bucket"] == org.bucket
    assert disk["createdBy"] == "Giulia"
    assert (await admin.get("/disks")).json() == [disk]
    formatted = formatter(ctx).formats[(org.valkey_url, 1)]
    assert (formatted["name"], formatted["block_size_kb"], formatted["trash_days"]) == (
        "archivio-2025",
        1024,
        14,
    )
    async with owner_engine.connect() as conn:
        detail = (
            await conn.execute(select(AuditEvent.detail).where(AuditEvent.action == "disk_created"))
        ).scalar_one()
    assert detail == {"blockSize": "1 MB"}
    assert (await admin.get("/session")).json()["organization"]["diskCount"] == 1


async def test_names_are_unique_case_insensitively_and_volume_names_forever(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    await create(admin, ctx, "Archivio")
    same = await admin.post("/disks", json={"name": "ARCHIVIO"})
    assert (same.status_code, same.json()["code"]) == (409, "disk_name_taken")
    async with owner_engine.begin() as conn:
        await conn.execute(update(Volume).values(deleted_at=text("now()")))
    # The display name is free again; the prefix and the database number are not.
    again = await create(admin, ctx, "Archivio")
    assert again["metaDb"] == 2
    assert await volume_names(owner_engine) == ["archivio", "archivio-2"]


async def test_disk_names_follow_the_folder_rule(org: Org, admin: httpx.AsyncClient) -> None:
    for bad in ("Archìvio", " Archivio", "Archivio ", "-x", "a/b", "x" * 61, ""):
        response = await admin.post("/disks", json={"name": bad})
        assert (response.status_code, response.json()["code"]) == (422, "validation"), bad
    for good in ("A", "Archivio 2025", "clip_finali.v2"):
        assert (await admin.post("/disks", json={"name": good})).status_code == 201, good


async def test_failed_format_burns_the_number_and_the_prefix(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    formatter(ctx).fail_format.add("rotto")
    failed = await create(admin, ctx, "Rotto")
    assert failed["state"] == "failed"
    assert failed["error"] == {
        "code": "format_failed",
        "message": "juicefs format: storage is not accessible",
    }
    assert (await admin.get("/disks")).json() == []
    formatter(ctx).fail_format.clear()
    next_disk = await create(admin, ctx, "Rotto")
    assert (next_disk["state"], next_disk["metaDb"]) == ("ready", 2)
    assert await volume_names(owner_engine) == ["rotto", "rotto-2"]


async def test_a_format_interrupted_by_a_crash_fails_after_a_while(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    created = (await admin.post("/disks", json={"name": "Archivio"})).json()
    async with owner_engine.begin() as conn:
        await conn.execute(update(Volume).values(created_at=text("now() - interval '1 hour'")))
    await expire_stale_rows(ctx)
    disk = (await admin.get(f"/disks/{created['id']}")).json()
    assert disk["state"] == "failed"
    assert disk["error"]["message"] == "The format was interrupted"
    assert await run_disk_formats(ctx) == 0


async def test_disk_limit_follows_valkey_databases(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(update(Organization).values(valkey_databases=3))
    for n in (1, 2):
        assert (await admin.post("/disks", json={"name": f"D{n}"})).status_code == 201
    full = await admin.post("/disks", json={"name": "D3"})
    assert full.status_code == 409
    assert full.json() == {
        "code": "disk_limit_reached",
        "message": "No Valkey database left for a new disk",
        "details": {"limit": 2},
    }


async def test_update_trash_through_juicefs_and_the_cache_policy(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    disk = await create(admin, ctx, "Archivio")
    response = await admin.patch(
        f"/disks/{disk['id']}", json={"trashDays": 30, "cache": {"expireDays": 7}}
    )
    assert response.status_code == 200
    assert (response.json()["trashDays"], response.json()["cache"]) == (30, {"expireDays": 7})
    assert formatter(ctx).formats[(org.valkey_url, 1)]["trash_days"] == 30
    formatter(ctx).fail_config.add("archivio")
    failed = await admin.patch(f"/disks/{disk['id']}", json={"trashDays": 1})
    assert failed.json()["code"] == "format_failed"
    assert (await admin.get("/disks")).json()[0]["trashDays"] == 30
    async with owner_engine.connect() as conn:
        details = (
            (
                await conn.execute(
                    select(AuditEvent.detail).where(AuditEvent.action == "disk_updated")
                )
            )
            .scalars()
            .all()
        )
    assert details == [{"trashDays": [7, 30], "cacheExpireDays": [30, 7]}]
    bad = await admin.patch(f"/disks/{disk['id']}", json={"cache": {"expireDays": 0}})
    assert bad.json()["code"] == "validation"


async def test_a_disk_still_creating_cannot_be_changed(org: Org, admin: httpx.AsyncClient) -> None:
    created = (await admin.post("/disks", json={"name": "Archivio"})).json()
    response = await admin.patch(f"/disks/{created['id']}", json={"trashDays": 3})
    assert (response.status_code, response.json()["code"]) == (409, "invalid_state")


async def test_mounted_by_counts_connected_macs_and_the_app_gets_the_disks(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk = await create(admin, ctx, "Archivio 2025")
    app = clients()
    mac = await enrol_mac(app, admin, ctx, org.admin_email)
    await app.post(
        "/device/heartbeat",
        json={"appVersion": "0.4.0", "macosVersion": "15.5", "mountedDiskIds": [disk["id"]]},
        headers=mac.auth,
    )
    assert (await admin.get("/disks")).json()[0]["mountedBy"] == 1
    config = (await app.get("/device/config", headers=mac.auth)).json()
    assert config["disks"] == [
        {
            "id": disk["id"],
            "name": "Archivio 2025",
            "volumeName": "archivio-2025",
            "metaDb": 1,
            "cacheExpireDays": 30,
            "mountPath": "/Volumes/Floppy/Archivio 2025",
        }
    ]


async def test_status_poller_reads_counters_and_reachability(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    await create(admin, ctx, "Archivio")
    valkey = ctx.metadata
    assert isinstance(valkey, FakeMetadataAdmin)
    valkey.counters[(org.valkey_url, 1)] = VolumeCounters(used_bytes=5_000_000, file_count=42)
    await poll_status(ctx, org.id)
    disk = (await admin.get("/disks")).json()[0]
    assert (disk["usedBytes"], disk["fileCount"], disk["status"]) == (5_000_000, 42, "online")
    assert disk["lastWriteAt"] is not None
    assert (await admin.get("/health")).json()["metadata"]["state"] == "ok"
    valkey.down = True
    await poll_status(ctx, org.id)
    assert (await admin.get("/disks")).json()[0]["status"] == "unreachable"
    assert (await admin.get("/health")).json()["metadata"]["state"] == "down"
