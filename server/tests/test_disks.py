"""
Disks: creation with its own instance, bucket and key, the creation job, names, buckets,
updates, counters. The meta service, JuiceFS and the probe are the labelled fakes; the real
pool, Valkey and Garage are in test_perdisk.py.
"""

from uuid import UUID

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.background import expire_stale_rows, poll_status, reconcile_instances
from floppy_server.context import Context
from floppy_server.disks.service import derive_volume_name, run_disk_formats
from floppy_server.infra.metadata import VolumeCounters
from floppy_server.infra.storage import FakeStorageProber
from floppy_server.models import AuditEvent, MetaInstance, S3Key, Volume, VolumeStatus
from tests.conftest import ClientFactory, Org
from tests.helpers import (
    create_disk,
    disk_key,
    enrol_device,
    fake_meta,
    fake_valkey,
    formatter,
    instance_url,
    invite_and_join,
)


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


async def test_create_probes_the_pasted_key_then_the_job_gives_the_disk_its_instance(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    body = {
        "name": "Archivio 2025",
        "trashDays": 14,
        "storage": {"bucket": "studio-archivio", "region": "eu-west", **disk_key(1)},
    }
    response = await admin.post("/disks", json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["state"] == "creating"
    assert created["mountPath"] == "/Volumes/Floppy/Archivio 2025"
    assert (created["bucket"], created["endpoint"], created["region"]) == (
        "studio-archivio",
        "https://s3.test",
        "eu-west",
    )
    assert created["metaUrl"] is None
    assert "volumeName" not in created
    assert isinstance(ctx.prober, FakeStorageProber)
    assert ctx.prober.probes == [("studio-archivio", "AKIATESTDISK00001")]
    # That write tested the storage: the disk is not "never probed" while it is created.
    async with owner_engine.connect() as conn:
        probed = (
            await conn.execute(select(VolumeStatus.storage_state, VolumeStatus.storage_probed_at))
        ).one()
    assert probed[0] == "ok"
    assert probed[1] is not None
    assert await run_disk_formats(ctx) == 1
    disk = (await admin.get(f"/disks/{created['id']}")).json()
    assert disk["state"] == "ready"
    assert disk["metaUrl"] == f"{instance_url(ctx, created['id'])}/1"
    assert (disk["blockSizeKb"], disk["trashDays"], disk["cache"]) == (1024, 14, {"expireDays": 30})
    assert disk["createdBy"] == "Giulia"
    assert (await admin.get("/disks")).json() == [disk]
    storage = (await admin.get("/health")).json()["disks"][0]["storage"]
    assert (storage["state"], storage["lastProbeAt"] is not None) == ("ok", True)
    # Manual: the disk's key goes into its own instance's format.
    formatted = formatter(ctx).formats[(instance_url(ctx, created["id"]), 1)]
    assert (formatted["name"], formatted["access_key_id"], formatted["trash_days"]) == (
        "archivio-2025",
        "AKIATESTDISK00001",
        14,
    )
    async with owner_engine.connect() as conn:
        key = (await conn.execute(select(S3Key.holder, S3Key.state, S3Key.volume_id))).one()
        actions = (
            await conn.execute(select(AuditEvent.action, AuditEvent.detail).order_by(AuditEvent.id))
        ).all()
        password = (await conn.execute(select(MetaInstance.admin_password_enc))).scalar_one()
    assert tuple(key) == ("disk", "active", UUID(created["id"]))
    assert ("disk_created", {"blockSize": "1 MB"}) in actions
    assert (
        "key_created",
        {"holder": "disk", "accessKeyTail": "00001", "for": "Archivio 2025"},
    ) in actions
    # The creator may write the new disk; nobody else is listed.
    assert (
        "disk_access_changed",
        {"person": "Giulia", "from": "none", "to": "write"},
    ) in actions
    assert password is not None
    assert b"fake-admin" not in password  # sealed, not stored in clear
    assert (await admin.get("/session")).json()["organization"]["diskCount"] == 1


async def test_a_refused_key_creates_nothing(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    response = await admin.post(
        "/disks",
        json={
            "name": "Archivio",
            "storage": {
                "bucket": "studio-archivio",
                "accessKeyId": "AKIADENIED0001",
                "secretKey": "x" * 12,
            },
        },
    )
    assert (response.status_code, response.json()["code"]) == (422, "probe_failed")
    assert response.json()["details"]["code"] == "AccessDenied"
    assert (await admin.get("/disks")).json() == []


async def test_manual_disks_need_their_bucket_and_key(org: Org, admin: httpx.AsyncClient) -> None:
    missing = await admin.post("/disks", json={"name": "Archivio"})
    assert (missing.status_code, missing.json()["code"]) == (422, "validation")
    bad_bucket = await admin.post(
        "/disks", json={"name": "Archivio", "storage": {"bucket": "Not A Bucket", **disk_key(2)}}
    )
    assert bad_bucket.status_code == 422


async def test_one_bucket_and_one_key_per_disk(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    await create_disk(admin, ctx, "Progetti", bucket="shared-bucket", key=disk_key(3))
    same_bucket = await admin.post(
        "/disks", json={"name": "Archivio", "storage": {"bucket": "shared-bucket", **disk_key(4)}}
    )
    assert (same_bucket.status_code, same_bucket.json()["code"]) == (409, "bucket_taken")
    same_key = await admin.post(
        "/disks", json={"name": "Archivio", "storage": {"bucket": "other-bucket", **disk_key(3)}}
    )
    assert (same_key.status_code, same_key.json()["code"]) == (422, "validation")


async def test_names_are_unique_case_insensitively_and_volume_names_forever(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    await create_disk(admin, ctx, "Archivio")
    same = await admin.post(
        "/disks", json={"name": "ARCHIVIO", "storage": {"bucket": "another", **disk_key(5)}}
    )
    assert (same.status_code, same.json()["code"]) == (409, "disk_name_taken")
    async with owner_engine.begin() as conn:
        await conn.execute(update(Volume).values(deleted_at=text("now()")))
    # The display name is free again; the prefix is not, and each disk has its own instance.
    again = await create_disk(admin, ctx, "Archivio")
    assert again["state"] == "ready"
    assert await volume_names(owner_engine) == ["archivio", "archivio-2"]
    assert len(fake_meta(ctx).instances) == 2


async def test_disk_names_follow_the_folder_rule(org: Org, admin: httpx.AsyncClient) -> None:
    for n, bad in enumerate(("Archìvio", " Archivio", "Archivio ", "-x", "a/b", "x" * 61, "")):
        response = await admin.post(
            "/disks", json={"name": bad, "storage": {"bucket": f"b-{n}-bad", **disk_key(n)}}
        )
        assert (response.status_code, response.json()["code"]) == (422, "validation"), bad
    for n, good in enumerate(("A", "Archivio 2025", "clip_finali.v2")):
        response = await admin.post(
            "/disks", json={"name": good, "storage": {"bucket": f"b-{n}-good", **disk_key(10 + n)}}
        )
        assert response.status_code == 201, good


async def test_a_failed_format_wipes_the_instance_and_frees_the_bucket(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    formatter(ctx).fail_format.add("rotto")
    failed = await create_disk(admin, ctx, "Rotto", bucket="rotto-bucket", key=disk_key(6))
    assert failed["state"] == "failed"
    assert failed["error"] == {
        "code": "format_failed",
        "message": "juicefs format: storage is not accessible",
    }
    assert (await admin.get("/disks")).json() == []
    assert fake_meta(ctx).released == [("fake-1", True)]
    async with owner_engine.connect() as conn:
        key_state = (await conn.execute(select(S3Key.state))).scalar_one()
        instance_state = (await conn.execute(select(MetaInstance.state))).scalar_one()
    assert (key_state, instance_state) == ("rejected", "stopped")
    formatter(ctx).fail_format.clear()
    # The same bucket and key work on a new try: the failed format never reached a device.
    next_disk = await create_disk(admin, ctx, "Rotto", bucket="rotto-bucket", key=disk_key(6))
    assert next_disk["state"] == "ready"
    assert await volume_names(owner_engine) == ["rotto", "rotto-2"]


async def test_the_meta_service_refusing_fails_the_disk(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    fake_meta(ctx).down = True
    failed = await create_disk(admin, ctx, "Archivio")
    assert failed["state"] == "failed"
    assert failed["error"]["message"].startswith("meta_unavailable")


async def test_a_creation_interrupted_by_a_crash_fails_after_a_while(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    created = await create_disk(admin, ctx, "Archivio", run=False)
    async with owner_engine.begin() as conn:
        await conn.execute(update(Volume).values(created_at=text("now() - interval '1 hour'")))
    await expire_stale_rows(ctx)
    disk = (await admin.get(f"/disks/{created['id']}")).json()
    assert disk["state"] == "failed"
    assert disk["error"]["message"] == "The creation was interrupted"
    assert await run_disk_formats(ctx) == 0


async def test_update_trash_through_juicefs_and_the_cache_policy(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    disk = await create_disk(admin, ctx, "Archivio")
    response = await admin.patch(
        f"/disks/{disk['id']}", json={"trashDays": 30, "cache": {"expireDays": 7}}
    )
    assert response.status_code == 200
    assert (response.json()["trashDays"], response.json()["cache"]) == (30, {"expireDays": 7})
    assert formatter(ctx).formats[(instance_url(ctx, disk["id"]), 1)]["trash_days"] == 30
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


async def test_a_disk_still_creating_cannot_be_changed(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    created = await create_disk(admin, ctx, "Archivio", run=False)
    response = await admin.patch(f"/disks/{created['id']}", json={"trashDays": 3})
    assert (response.status_code, response.json()["code"]) == (409, "invalid_state")


async def test_mounted_by_counts_connected_devices(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk = await create_disk(admin, ctx, "Archivio 2025")
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    await app.post(
        "/device/heartbeat",
        json={"appVersion": "0.4.0", "macosVersion": "15.5", "mountedDiskIds": [disk["id"]]},
        headers=device.auth,
    )
    assert (await admin.get("/disks")).json()[0]["mountedBy"] == 1


async def test_status_poller_reads_each_instance_and_the_probe_each_bucket(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    disk = await create_disk(admin, ctx, "Archivio")
    other = await create_disk(admin, ctx, "Progetti")
    url = instance_url(ctx, disk["id"])
    fake_valkey(ctx).counters[url] = VolumeCounters(used_bytes=5_000_000, file_count=42)
    await poll_status(ctx, org.id)
    listed = {d["name"]: d for d in (await admin.get("/disks")).json()}
    assert (listed["Archivio"]["usedBytes"], listed["Archivio"]["fileCount"]) == (5_000_000, 42)
    assert listed["Archivio"]["lastWriteAt"] is not None
    assert listed["Progetti"]["usedBytes"] == 0
    health = (await admin.get("/health")).json()
    assert health["metadata"]["state"] == "ok"
    assert [d["name"] for d in health["disks"]] == ["Archivio", "Progetti"]
    # One instance down: that disk only, and the summary says so.
    fake_valkey(ctx).down.add(url)
    await poll_status(ctx, org.id)
    listed = {d["name"]: d for d in (await admin.get("/disks")).json()}
    assert (listed["Archivio"]["status"], listed["Progetti"]["status"]) == ("unreachable", "online")
    health = (await admin.get("/health")).json()
    assert health["metadata"]["state"] == "down"
    assert {d["name"]: d["metadata"]["state"] for d in health["disks"]} == {
        "Archivio": "down",
        "Progetti": "ok",
    }
    # The probe of one disk's bucket lands in its storage status.
    assert (await admin.post(f"/disks/{other['id']}/probe")).status_code == 204
    health = (await admin.get("/health")).json()
    by_name = {d["name"]: d["storage"]["state"] for d in health["disks"]}
    assert by_name["Progetti"] == "ok"


async def test_deleting_a_disk_takes_it_off_every_device_and_keeps_its_data(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    disk = await create_disk(admin, ctx, "Archivio", bucket="studio-archivio")
    kept = await create_disk(admin, ctx, "Progetti")
    app = clients()
    device = await enrol_device(app, admin, ctx, org.admin_email)
    url = instance_url(ctx, disk["id"])
    valkey = fake_valkey(ctx)
    assert valkey.access(url, device.valkey_user) == "write"

    member = clients()
    await invite_and_join(admin, member, ctx, "sara@studio-a.test")
    assert (await member.delete(f"/disks/{disk['id']}")).status_code == 403

    response = await admin.delete(f"/disks/{disk['id']}")
    assert response.status_code == 204, response.text
    # Off the lists and off the device: its user deleted and its connections killed.
    assert [d["name"] for d in (await admin.get("/disks")).json()] == ["Progetti"]
    assert {e["diskId"] for e in (await admin.get("/access")).json()} == {kept["id"]}
    assert valkey.access(url, device.valkey_user) is None
    assert device.valkey_user in valkey.killed
    beat = (
        await app.post(
            "/device/heartbeat",
            json={"appVersion": "0.4.0", "macosVersion": "15.5"},
            headers=device.auth,
        )
    ).json()
    assert beat["disks"] == [{"id": kept["id"], "access": "write"}]
    # Its instance stays, and the reconciler does not bring the user back.
    assert fake_meta(ctx).instances[disk["id"]].url == url
    await reconcile_instances(ctx, org.id)
    assert valkey.access(url, device.valkey_user) is None
    assert (await admin.delete(f"/disks/{disk['id']}")).status_code == 404

    async with owner_engine.connect() as conn:
        detail = (
            await conn.execute(select(AuditEvent.detail).where(AuditEvent.action == "disk_deleted"))
        ).scalar_one()
    assert detail == {"bucket": "studio-archivio"}
    # The name is free again; the bucket is not, its objects are still there.
    again = await create_disk(admin, ctx, "Archivio")
    assert again["state"] == "ready"
    assert await volume_names(owner_engine) == ["archivio", "progetti", "archivio-2"]
    same_bucket = await admin.post(
        "/disks", json={"name": "Altro", "storage": {"bucket": "studio-archivio", **disk_key(9)}}
    )
    assert (same_bucket.status_code, same_bucket.json()["code"]) == (409, "bucket_taken")


async def test_a_disk_still_creating_cannot_be_deleted(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    created = await create_disk(admin, ctx, "Archivio", run=False)
    response = await admin.delete(f"/disks/{created['id']}")
    assert (response.status_code, response.json()["code"]) == (409, "invalid_state")
