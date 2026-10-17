"""
`ValkeyMetadataAdmin` against the real Valkey of the Compose file: a Mac's user can do what
JuiceFS and its replica need and nothing administrative; deleting it cuts it off at once.
"""

import asyncio
import hashlib
import os
import secrets
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as aioredis
from pydantic import SecretStr
from redis.exceptions import AuthenticationError, NoPermissionError, ResponseError
from redis.exceptions import ConnectionError as RedisConnectionError

from floppy_server.background import reconcile_acl
from floppy_server.context import Context
from floppy_server.infra.metadata import (
    MetadataUnavailableError,
    ValkeyMetadataAdmin,
    ValkeyTarget,
)
from tests.conftest import VALKEY_URL, ClientFactory, make_org
from tests.helpers import enrol_mac, sign_in


@pytest.fixture
def target() -> ValkeyTarget:
    password = os.environ.get("FLOPPY_VALKEY_ADMIN_PASSWORD")
    if not password:
        pytest.skip("FLOPPY_VALKEY_ADMIN_PASSWORD is not set")
    return ValkeyTarget(url=VALKEY_URL, username=None, password=password)


@pytest.fixture
async def device_user(target: ValkeyTarget) -> AsyncIterator[tuple[str, str]]:
    """A fresh device user name and password; removed afterwards."""
    username = "floppy_d_" + uuid.uuid4().hex
    yield username, secrets.token_urlsafe(32)
    await ValkeyMetadataAdmin().delete_device_user(target, username)


def mac_client(username: str, password: str, db: int = 1) -> aioredis.Redis:
    return aioredis.Redis.from_url(VALKEY_URL, username=username, password=password, db=db)


async def test_ping(target: ValkeyTarget) -> None:
    assert await ValkeyMetadataAdmin().ping(target) >= 0


async def test_a_mac_user_writes_metadata_but_cannot_administer(
    target: ValkeyTarget, device_user: tuple[str, str]
) -> None:
    username, password = device_user
    admin = ValkeyMetadataAdmin()
    await admin.set_device_user(target, username, hashlib.sha256(password.encode()).digest())
    users = await admin.device_users(target)
    assert users[username] == {hashlib.sha256(password.encode()).digest()}

    mac = mac_client(username, password)
    try:
        await mac.set("floppy-test-key", "1")
        assert await mac.hincrby("ryw-test", "n", 1) >= 1
        assert await mac.eval("return 1", 0) == 1
        assert await mac.ping()
        await mac.delete("floppy-test-key", "ryw-test")
        for command in (
            ("ACL", "LIST"),
            ("CONFIG", "SET", "maxmemory", "1"),
            ("FLUSHALL",),
            ("FLUSHDB",),
            ("SHUTDOWN",),
        ):
            with pytest.raises((NoPermissionError, ResponseError)):
                await mac.execute_command(*command)
    finally:
        await mac.aclose()


async def test_deleting_the_user_cuts_the_mac_off(
    target: ValkeyTarget, device_user: tuple[str, str]
) -> None:
    username, password = device_user
    admin = ValkeyMetadataAdmin()
    await admin.set_device_user(target, username, hashlib.sha256(password.encode()).digest())
    mac = mac_client(username, password)
    try:
        assert await mac.ping()
        await admin.delete_device_user(target, username)
        await asyncio.sleep(0.1)
        with pytest.raises((RedisConnectionError, AuthenticationError, ResponseError)):
            # The open connection was killed; a reconnection is refused.
            await mac.set("after-revoke", "1")
        assert username not in await admin.device_users(target)
        # Deleting again is not an error.
        await admin.delete_device_user(target, username)
    finally:
        await mac.aclose()


async def test_a_new_password_replaces_the_old_one(
    target: ValkeyTarget, device_user: tuple[str, str]
) -> None:
    username, first = device_user
    second = secrets.token_urlsafe(32)
    admin = ValkeyMetadataAdmin()
    await admin.set_device_user(target, username, hashlib.sha256(first.encode()).digest())
    await admin.set_device_user(target, username, hashlib.sha256(second.encode()).digest())
    old = mac_client(username, first)
    new = mac_client(username, second)
    try:
        with pytest.raises(AuthenticationError):
            await old.ping()
        assert await new.ping()
    finally:
        await old.aclose()
        await new.aclose()


async def test_counters_come_from_the_volume_database(target: ValkeyTarget) -> None:
    db = 15
    client = aioredis.Redis.from_url(VALKEY_URL, password=target.password, db=db)
    try:
        await client.mset({"usedSpace": 1234, "totalInodes": 7})
        counters = await ValkeyMetadataAdmin().volume_counters(target, db)
        assert (counters.used_bytes, counters.file_count) == (1234, 7)
    finally:
        await client.delete("usedSpace", "totalInodes")
        await client.aclose()


async def test_wrong_credentials_are_unavailable(target: ValkeyTarget) -> None:
    wrong = ValkeyTarget(url=target.url, username=None, password="wrong")
    with pytest.raises(MetadataUnavailableError):
        await ValkeyMetadataAdmin().ping(wrong)


async def test_only_generated_names_reach_a_command(target: ValkeyTarget) -> None:
    with pytest.raises(ValueError, match="not a device user name"):
        await ValkeyMetadataAdmin().delete_device_user(target, "default")


async def test_enrolment_revocation_and_reconciler_on_real_valkey(
    target: ValkeyTarget, ctx: Context, clients: ClientFactory
) -> None:
    ctx.metadata = ValkeyMetadataAdmin()
    ctx.settings.valkey_admin_password = SecretStr(target.password or "")
    org = await make_org(ctx, "studio-v", "vera@v.test", valkey_url=VALKEY_URL)
    admin = clients()
    await sign_in(admin, ctx, org.admin_email)
    mac = await enrol_mac(clients(), admin, ctx, org.admin_email)
    assert mac.valkey_password
    probe = mac_client(mac.valkey_user, mac.valkey_password)
    raw = aioredis.Redis.from_url(VALKEY_URL, password=target.password)
    try:
        assert await probe.ping()
        # A failover to a replica loses ACL users: the reconciler puts them back.
        await raw.execute_command("ACL", "DELUSER", mac.valkey_user)
        await reconcile_acl(ctx, org.id)
        assert mac.valkey_user in await ctx.metadata.device_users(target)
        await probe.aclose()
        # DELUSER closed the old connection; the Mac reconnects with the same password.
        probe = mac_client(mac.valkey_user, mac.valkey_password)
        assert await probe.ping()
        response = await admin.post(f"/macs/{mac.id}/revoke")
        assert response.status_code == 200
        assert mac.valkey_user not in await ctx.metadata.device_users(target)
        with pytest.raises((RedisConnectionError, AuthenticationError, ResponseError)):
            await probe.ping()
    finally:
        await probe.aclose()
        await raw.aclose()
