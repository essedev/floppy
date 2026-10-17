"""
`ValkeyMetadataAdmin` and `PoolMetaService` against the real Valkey instances of the tests'
pool (Compose profile `test`): a read-only user reads and replicates but every write is
refused, a read-write user writes, an access change applies to an open connection, deleting
the user cuts it off, and the pool hands each disk its own instance.
"""

import asyncio
import hashlib
import os
import secrets
import uuid

import pytest
import redis.asyncio as aioredis
from redis.exceptions import AuthenticationError, NoPermissionError, ResponseError
from redis.exceptions import ConnectionError as RedisConnectionError

from floppy_server.infra.meta import MetaServiceError, PoolMetaService, parse_pool
from floppy_server.infra.metadata import (
    META_DB,
    MetadataUnavailableError,
    ValkeyMetadataAdmin,
    ValkeyTarget,
    access_of,
)

# Every write a tampered device could try, as spike/perdisk.py t19 lists them.
WRITES: list[tuple[str, ...]] = [
    ("SET", "x", "1"),
    ("HSET", "i1", "x", "1"),
    ("DEL", "setting"),
    ("INCR", "nextinode"),
    ("HINCRBY", "ryw1", "n", "1"),
    ("EVAL", "return redis.call('set','x','1')", "0"),
    ("MULTI",),
    ("FLUSHDB",),
    ("CONFIG", "SET", "appendonly", "no"),
    ("SCRIPT", "FLUSH"),
    ("ACL", "SETUSER", "someone", "+@all"),
    ("RESTORE", "x", "0", "x"),
]


def target(url: str) -> ValkeyTarget:
    return ValkeyTarget(url=url, username=None, password=os.environ["FLOPPY_VALKEY_ADMIN_PASSWORD"])


def device(url: str, username: str, password: str) -> aioredis.Redis:
    return aioredis.Redis.from_url(url, username=username, password=password, db=META_DB)


def new_user() -> tuple[str, str, bytes]:
    password = secrets.token_urlsafe(32)
    return "floppy_d_" + uuid.uuid4().hex, password, hashlib.sha256(password.encode()).digest()


async def test_a_read_only_user_reads_and_replicates_but_cannot_write(pool: list[str]) -> None:
    url = pool[0]
    admin = ValkeyMetadataAdmin()
    raw = aioredis.Redis.from_url(url, password=target(url).password, db=META_DB)
    await raw.set("setting", '{"Name": "progetti"}')
    username, password, digest = new_user()
    await admin.set_device_user(target(url), username, digest, "read")
    users = await admin.device_users(target(url))
    assert users[username].passwords == {digest}
    assert users[username].access == "read"
    client = device(url, username, password)
    try:
        assert await client.get("setting") == b'{"Name": "progetti"}'
        assert await client.ping()
        assert (await client.info("replication"))["role"] == "master"
        # What a replica sends to its master to sync: allowed (the reply is the master's).
        assert await client.execute_command("REPLCONF", "listening-port", "6380") == b"OK"
        refused = []
        for command in WRITES:
            try:
                await client.execute_command(*command)
            except (NoPermissionError, ResponseError) as error:
                refused.append("NOPERM" in str(error) or "permission" in str(error).lower())
            else:
                refused.append(False)
        assert all(refused), list(zip(WRITES, refused, strict=True))
        assert await raw.get("setting") == b'{"Name": "progetti"}'
    finally:
        await client.aclose()
        await raw.aclose()


async def test_a_read_write_user_writes_and_runs_the_optimistic_scripts(pool: list[str]) -> None:
    url = pool[0]
    admin = ValkeyMetadataAdmin()
    username, password, digest = new_user()
    await admin.set_device_user(target(url), username, digest, "write")
    assert (await admin.device_users(target(url)))[username].access == "write"
    client = device(url, username, password)
    try:
        await client.set("floppy-test-key", "1")
        assert await client.hincrby("ryw-test", "n", 1) >= 1
        assert await client.eval("return 1", 0) == 1
        sha = await client.script_load("return 2")
        assert await client.evalsha(sha, 0) == 2
        async with client.pipeline(transaction=True) as tx:
            tx.set("in-tx", "1")
            await tx.execute()
        for command in (
            ("FLUSHALL",),
            ("FLUSHDB",),
            ("ACL", "LIST"),
            ("CONFIG", "SET", "maxmemory", "1"),
        ):
            with pytest.raises((NoPermissionError, ResponseError)):
                await client.execute_command(*command)
    finally:
        await client.aclose()


async def test_lowering_access_applies_to_the_open_connection(pool: list[str]) -> None:
    url = pool[0]
    admin = ValkeyMetadataAdmin()
    username, password, digest = new_user()
    await admin.set_device_user(target(url), username, digest, "write")
    client = device(url, username, password)
    try:
        await client.set("before", "1")
        await admin.set_device_user(target(url), username, digest, "read")
        # The same connection, not closed: its next write is refused, reads go on.
        with pytest.raises(NoPermissionError):
            await client.set("after", "1")
        assert await client.get("before") == b"1"
        await admin.set_device_user(target(url), username, digest, "write")
        await client.set("after", "1")
    finally:
        await client.aclose()


async def test_a_new_password_closes_old_connections_and_deleting_cuts_off(
    pool: list[str],
) -> None:
    url = pool[0]
    admin = ValkeyMetadataAdmin()
    username, first, digest = new_user()
    second = secrets.token_urlsafe(32)
    await admin.set_device_user(target(url), username, digest, "write")
    old = device(url, username, first)
    try:
        assert await old.ping()
        await admin.set_device_user(
            target(url), username, hashlib.sha256(second.encode()).digest(), "write"
        )
        await asyncio.sleep(0.1)
        with pytest.raises((RedisConnectionError, AuthenticationError, ResponseError)):
            await old.ping()
        new = device(url, username, second)
        assert await new.ping()
        await admin.delete_device_user(target(url), username)
        await asyncio.sleep(0.1)
        with pytest.raises((RedisConnectionError, AuthenticationError, ResponseError)):
            await new.set("after-revoke", "1")
        await new.aclose()
        assert username not in await admin.device_users(target(url))
        await admin.delete_device_user(target(url), username)  # already gone: not an error
    finally:
        await old.aclose()


async def test_counters_come_from_database_one(pool: list[str]) -> None:
    url = pool[1]
    raw = aioredis.Redis.from_url(url, password=target(url).password, db=META_DB)
    try:
        await raw.mset({"usedSpace": 1234, "totalInodes": 7})
        counters = await ValkeyMetadataAdmin().volume_counters(target(url))
        assert (counters.used_bytes, counters.file_count) == (1234, 7)
    finally:
        await raw.aclose()


async def test_wrong_credentials_and_names_are_refused(pool: list[str]) -> None:
    wrong = ValkeyTarget(url=pool[0], username=None, password="wrong")
    with pytest.raises(MetadataUnavailableError):
        await ValkeyMetadataAdmin().ping(wrong)
    with pytest.raises(ValueError, match="not a device user name"):
        await ValkeyMetadataAdmin().delete_device_user(target(pool[0]), "default")


def test_access_is_read_from_the_command_list() -> None:
    assert access_of(b"-@all +@read +@write +@transaction -@dangerous +ping") == "write"
    assert access_of("-@all +@read -@dangerous +info +psync") == "read"
    assert access_of("+@all") is None


async def test_the_pool_gives_each_disk_its_own_instance_and_never_one_with_data(
    pool: list[str],
) -> None:
    password = os.environ["FLOPPY_VALKEY_ADMIN_PASSWORD"]
    entries = parse_pool(",".join(f"{u}|{u}" for u in pool))
    meta = PoolMetaService(entries, password)
    # One instance already holds data nobody claimed: it is never handed out.
    raw = aioredis.Redis.from_url(pool[2], password=password, db=META_DB)
    await raw.set("setting", "an old tree")
    await raw.aclose()
    first = await meta.create_instance("disk-a")
    assert await meta.create_instance("disk-a") == first  # idempotent by name
    second = await meta.create_instance("disk-b")
    assert {first.url, second.url} == {pool[0], pool[1]}
    assert first.admin_password == password
    with pytest.raises(MetaServiceError, match="no free instance"):
        await meta.create_instance("disk-c")
    # A released instance keeps its data and its claim; a discarded one is free again.
    await meta.release_instance(first.service_ref, discard=False)
    with pytest.raises(MetaServiceError):
        await meta.create_instance("disk-c")
    await meta.release_instance(second.service_ref, discard=True)
    assert (await meta.create_instance("disk-c")).url == second.url
    with pytest.raises(ValueError, match="no credentials"):
        parse_pool("redis://user:pw@host:6379")
