"""
Per-disk access end to end, through the API, on the real systems: the tests' meta pool gives
each disk its own Valkey instance, `ValkeyMetadataAdmin` puts each device's user there with
the ACL of its access, and Garage gives each device its own key granted per bucket (managed
mode). What the device can then do is checked with its own credentials, the way its replica,
its JuiceFS mount and its S3 client would. JuiceFS itself is the labelled fake here; the real
format is in test_juicefs_s3.py.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

import boto3
import pytest
import redis.asyncio as aioredis
from botocore.exceptions import ClientError
from redis.exceptions import AuthenticationError, NoPermissionError, ResponseError
from redis.exceptions import ConnectionError as RedisConnectionError

from floppy_server.background import reconcile_instances
from floppy_server.context import Context
from floppy_server.infra.meta import PoolMetaService, parse_pool
from floppy_server.infra.metadata import ValkeyMetadataAdmin
from floppy_server.infra.providers import GarageAdapter
from floppy_server.infra.storage import S3StorageProber
from floppy_server.models import Organization
from tests.conftest import ClientFactory, Org, make_org
from tests.helpers import create_disk, enrol_device, invite_and_join, set_access, sign_in
from tests.test_garage import ADMIN_URL, S3_URL, cleanup

DENIED = (NoPermissionError, ResponseError)
CUT_OFF = (RedisConnectionError, AuthenticationError, ResponseError)


@pytest.fixture
async def real(ctx: Context, pool: list[str]) -> AsyncIterator[Context]:
    """The test context on the real pool, Valkey, Garage and S3 probe."""
    token = os.environ.get("FLOPPY_GARAGE_ADMIN_TOKEN")
    if not token:
        pytest.skip("FLOPPY_GARAGE_ADMIN_TOKEN is not set: run docker/garage-setup.sh")
    password = os.environ["FLOPPY_VALKEY_ADMIN_PASSWORD"]
    ctx.metadata = ValkeyMetadataAdmin()
    ctx.meta = PoolMetaService(parse_pool(",".join(pool)), password)
    ctx.adapter_override = GarageAdapter(ADMIN_URL, token, timeout_seconds=5)
    ctx.prober = S3StorageProber(timeout_seconds=5)
    yield ctx
    await cleanup(token)


def meta_client(disk: dict[str, Any], username: str, password: str) -> aioredis.Redis:
    """The device's connection to a disk's instance, as its JuiceFS mount opens it."""
    return aioredis.Redis.from_url(disk["metaUrl"], username=username, password=password)


def s3(key: dict[str, str]) -> Any:
    return boto3.session.Session().client(
        "s3",
        endpoint_url=S3_URL,
        region_name="garage",
        aws_access_key_id=key["accessKeyId"],
        aws_secret_access_key=key["secretKey"],
    )


def can(action: str, key: dict[str, str], bucket: str) -> bool:
    client = s3(key)
    try:
        if action == "list":
            client.list_objects_v2(Bucket=bucket)
        else:
            client.put_object(Bucket=bucket, Key="floppy-perdisk-probe", Body=b"x")
    except ClientError:
        return False
    finally:
        client.close()
    return True


async def writes(client: aioredis.Redis) -> bool:
    try:
        await client.set("floppy-perdisk", "1")
    except DENIED:
        return False
    return True


async def test_managed_access_on_the_real_instances_and_buckets(
    real: Context, clients: ClientFactory
) -> None:
    ctx = real
    org: Org = await make_org(
        ctx,
        "studio-r",
        "rita@r.test",
        storage_mode="managed",
        storage_provider="garage",
    )
    async with ctx.sessions() as db:
        row = await db.get(Organization, org.id)
        assert row is not None
        row.s3_endpoint, row.s3_region = S3_URL, "garage"
        await db.commit()
    admin = clients()
    await sign_in(admin, ctx, org.admin_email)
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@r.test")
    progetti = await create_disk(admin, ctx, "Progetti", managed=True)
    archivio = await create_disk(admin, ctx, "Archivio", managed=True)
    riservato = await create_disk(admin, ctx, "Riservato", managed=True)
    assert [d["state"] for d in (progetti, archivio, riservato)] == ["ready"] * 3
    # Three disks, three instances of the pool.
    assert len({d["metaUrl"] for d in (progetti, archivio, riservato)}) == 3
    await set_access(admin, progetti["id"], person["id"], "write")
    await set_access(admin, archivio["id"], person["id"], "read")

    app = clients()
    device = await enrol_device(app, marco, ctx, "marco@r.test", name="iMac di Marco")
    assert device.valkey_password and device.s3
    assert [(d["name"], d["access"]) for d in device.disks] == [
        ("Progetti", "write"),
        ("Archivio", "read"),
    ]
    user, password, key = device.valkey_user, device.valkey_password, device.s3
    rw = meta_client(progetti, user, password)
    ro = meta_client(archivio, user, password)
    hidden = meta_client(riservato, user, password)
    try:
        # Metadata: write where it may write, read-only refuses every write, no user at all
        # on the disk it may not see.
        assert await writes(rw)
        assert await ro.ping()
        assert not await writes(ro)
        with pytest.raises(CUT_OFF):
            await hidden.ping()
        # Storage: the device's own key, granted per bucket.
        assert can("put", key, progetti["bucket"])
        assert can("list", key, archivio["bucket"])
        assert not can("put", key, archivio["bucket"])
        assert not can("list", key, riservato["bucket"])

        # Lowered live: the open connection loses its writes, the key its put; the heartbeat
        # tells the app to remount read-only.
        lowered = await set_access(admin, progetti["id"], person["id"], "read")
        assert lowered["applied"] is True
        assert not await writes(rw)
        assert await rw.ping()
        assert not can("put", key, progetti["bucket"])
        assert can("list", key, progetti["bucket"])
        beat = await app.post(
            "/device/heartbeat",
            json={"appVersion": "0.4.0", "macosVersion": "15.5"},
            headers=device.auth,
        )
        assert {d["id"]: d["access"] for d in beat.json()["disks"]}[progetti["id"]] == "read"

        # Raised again: writes come back without a reconnection.
        await set_access(admin, progetti["id"], person["id"], "write")
        assert await writes(rw)

        # Revoked: cut off everywhere, the key deleted at Garage.
        revoked = await admin.post(f"/devices/{device.id}/revoke")
        assert revoked.status_code == 200
        for disk in (progetti, archivio):
            fresh = meta_client(disk, user, password)
            with pytest.raises(CUT_OFF):
                await fresh.ping()
            await fresh.aclose()
        assert not can("list", key, progetti["bucket"])
    finally:
        for client in (rw, ro, hidden):
            await client.aclose()


async def test_the_reconciler_rebuilds_users_on_a_restored_instance(
    real: Context, clients: ClientFactory, pool: list[str]
) -> None:
    ctx = real
    ctx.adapter_override = None
    org = await make_org(ctx, "studio-s", "sara@s.test")
    admin = clients()
    await sign_in(admin, ctx, org.admin_email)
    key_id, secret = (
        os.environ.get("FLOPPY_TEST_S3_ACCESS_KEY_ID"),
        os.environ.get("FLOPPY_TEST_S3_SECRET_KEY"),
    )
    if not key_id or not secret:
        pytest.skip("FLOPPY_TEST_S3_* are not set: run docker/garage-setup.sh")
    async with ctx.sessions() as db:
        row = await db.get(Organization, org.id)
        assert row is not None
        row.s3_endpoint, row.s3_region = S3_URL, "garage"
        await db.commit()
    disk = await create_disk(
        admin,
        ctx,
        "Archivio",
        bucket=os.environ.get("FLOPPY_TEST_S3_BUCKET", "floppy-dev"),
        key={"accessKeyId": key_id, "secretKey": secret},
    )
    assert disk["state"] == "ready", disk
    device = await enrol_device(clients(), admin, ctx, org.admin_email)
    assert device.valkey_password
    assert device.s3 is None  # manual: the key is in the disk's format
    raw = aioredis.Redis.from_url(
        disk["metaUrl"].rsplit("/", 1)[0], password=os.environ["FLOPPY_VALKEY_ADMIN_PASSWORD"]
    )
    try:
        # A master rebuilt from a dump: no ACL users. The reconciler recreates them from
        # the hashes, and the device's same password works again.
        await raw.execute_command("ACL", "DELUSER", device.valkey_user)
        await reconcile_instances(ctx, org.id)
        client = meta_client(disk, device.valkey_user, device.valkey_password)
        assert await writes(client)
        await client.aclose()
    finally:
        await raw.aclose()
