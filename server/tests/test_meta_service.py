"""
`HttpMetaService` against the real meta service (the `meta-test` Compose service, profile
`test`): instances created idempotently by name, reached by the control plane on the private
network with the admin password the service returned, kept or discarded on release; and a
disk created through the API on it, with the device's user put on its own instance by
`ValkeyMetadataAdmin`. Devices' TLS route through Traefik is checked in deploy/check.
"""

import hashlib
import os
import re
import secrets
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from floppy_server.context import Context
from floppy_server.infra.meta import HttpMetaService, InstanceSpec, MetaServiceError
from floppy_server.infra.metadata import ValkeyMetadataAdmin, ValkeyTarget
from floppy_server.models import MetaInstance
from tests.conftest import ClientFactory, Org
from tests.helpers import create_disk, enrol_device, invite_and_join, set_access

META_URL = os.environ.get("FLOPPY_TEST_META_URL", "http://127.0.0.1:4565")
# The meta-test service's fixed token (server/docker-compose.yml): a test value, not a secret.
META_TOKEN = os.environ.get("FLOPPY_TEST_META_TOKEN", "floppy-meta-test-token-not-a-secret-0001")
LABEL = r"rediss://[a-z2-7]{16}\.meta\.floppy\.test:443"


def _names() -> set[str]:
    response = httpx.get(
        f"{META_URL}/v1/instances", headers={"authorization": f"Bearer {META_TOKEN}"}, timeout=5
    )
    response.raise_for_status()
    return {item["name"] for item in response.json()}


@pytest.fixture
async def meta() -> AsyncIterator[HttpMetaService]:
    """The client on the tests' meta service; every instance a test made is discarded after."""
    try:
        before = _names()
    except httpx.HTTPError:
        pytest.fail(
            f"the meta service does not answer at {META_URL}: "
            "docker compose --profile test up -d --build meta-test"
        )
    service = HttpMetaService(META_URL, META_TOKEN, admin_host="127.0.0.1")
    yield service
    for made in _names() - before:
        await service.release_instance(made, discard=True)


def name() -> str:
    return f"test-{secrets.token_hex(8)}"


def admin_client(spec: InstanceSpec, db: int = 1) -> aioredis.Redis:
    assert spec.admin_url is not None
    return aioredis.Redis.from_url(spec.admin_url, password=spec.admin_password, db=db)


async def test_instances_are_created_once_and_reached_on_the_private_network(
    meta: HttpMetaService,
) -> None:
    disk = name()
    spec = await meta.create_instance(disk)
    assert spec.service_ref == disk
    assert re.fullmatch(LABEL, spec.url)
    assert spec.admin_url is not None
    assert spec.admin_url.startswith("redis://127.0.0.1:456")
    assert spec.admin_username is None
    assert spec.admin_password
    assert spec.admin_password not in repr(spec)
    assert await meta.create_instance(disk) == spec  # idempotent by name
    other = await meta.create_instance(name())
    assert other.url != spec.url
    assert other.admin_url != spec.admin_url
    assert other.admin_password != spec.admin_password
    client = admin_client(spec)
    try:
        await client.set("setting", "tree")
        # The disk's name is nowhere in what devices see.
        assert disk not in spec.url
    finally:
        await client.aclose()


async def test_release_keeps_the_data_and_discard_wipes_it(meta: HttpMetaService) -> None:
    disk = name()
    spec = await meta.create_instance(disk)
    client = admin_client(spec)
    await client.set("setting", "tree")
    await client.aclose()
    await meta.release_instance(spec.service_ref, discard=False)
    stopped = admin_client(spec)
    with pytest.raises(RedisConnectionError):
        await stopped.ping()
    await stopped.aclose()
    # Asked again, the service starts the same instance with its data.
    assert await meta.create_instance(disk) == spec
    client = admin_client(spec)
    assert await client.get("setting") == b"tree"
    await client.aclose()
    await meta.release_instance(spec.service_ref, discard=True)
    with pytest.raises(MetaServiceError, match="404"):
        await meta.release_instance(spec.service_ref, discard=True)
    fresh = await meta.create_instance(disk)
    assert fresh.url != spec.url
    client = admin_client(fresh)
    assert await client.get("setting") is None
    await client.aclose()


async def test_a_wrong_token_or_an_unreachable_service_is_a_meta_error() -> None:
    with pytest.raises(MetaServiceError, match="401"):
        await HttpMetaService(META_URL, "wrong-token").create_instance(name())
    with pytest.raises(MetaServiceError, match="unreachable"):
        await HttpMetaService("http://127.0.0.1:9", META_TOKEN, timeout_seconds=2).create_instance(
            name()
        )
    with pytest.raises(ValueError, match="no credentials"):
        HttpMetaService("http://user:pw@meta:8080", META_TOKEN)


async def test_the_admin_operations_work_on_a_service_instance(meta: HttpMetaService) -> None:
    """ACL users in the instance's ACL file: what the control plane runs on every instance."""
    spec = await meta.create_instance(name())
    target = ValkeyTarget(spec.admin_url or spec.url, None, spec.admin_password)
    admin = ValkeyMetadataAdmin()
    user, password = "floppy_d_" + secrets.token_hex(16), secrets.token_urlsafe(16)
    digest = hashlib.sha256(password.encode()).digest()
    assert await admin.ping(target) >= 0
    await admin.set_device_user(target, user, digest, "read")
    assert (await admin.device_users(target))[user].access == "read"
    await admin.set_device_user(target, user, digest, "write")
    device = aioredis.Redis.from_url(spec.admin_url or "", username=user, password=password, db=1)
    try:
        await device.set("written", "by the device")
    finally:
        await device.aclose()
    await admin.delete_device_user(target, user)
    assert user not in await admin.device_users(target)
    counters = await admin.volume_counters(target)
    assert counters.file_count == 0


async def test_a_disk_on_the_meta_service_gets_its_instance_and_its_devices_users(
    ctx: Context, org: Org, admin: httpx.AsyncClient, clients: ClientFactory, meta: HttpMetaService
) -> None:
    ctx.meta = meta
    ctx.metadata = ValkeyMetadataAdmin()
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    disk: dict[str, Any] = await create_disk(admin, ctx, "Progetti")
    assert disk["state"] == "ready"
    assert re.fullmatch(LABEL + "/1", disk["metaUrl"])
    await set_access(admin, disk["id"], person["id"], "write")
    device = await enrol_device(clients(), marco, ctx, "marco@studio-a.test")
    assert device.valkey_password
    async with ctx.sessions() as db:
        instance = (
            await db.execute(select(MetaInstance).where(MetaInstance.volume_id == disk["id"]))
        ).scalar_one()
    assert instance.admin_url is not None
    assert instance.url == disk["metaUrl"].removesuffix("/1")
    assert instance.admin_password_enc is not None  # sealed, never in clear (DECISIONS #17)
    # The device's own user on the disk's instance, with write access.
    client = aioredis.Redis.from_url(
        instance.admin_url, username=device.valkey_user, password=device.valkey_password, db=1
    )
    try:
        await client.set("setting", "tree")
    finally:
        await client.aclose()
