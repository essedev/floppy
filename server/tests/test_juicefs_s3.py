"""
The real `JuiceFSFormatter` and `S3StorageProber` against Garage and a disk's own Valkey
instance (the first of the tests' pool). JuiceFS is Linux only: the tests run the server
image's binary with `docker run` on the Compose network, so the instance's address is the
container's (`meta-test-1:6379`) and Garage's is `FLOPPY_TEST_S3_URL_IN_DOCKER` (the Compose
`garage:3900` by default); the checks read Valkey and Garage from the host.

Needs `docker compose --profile test up -d db meta-test-1`, `docker/garage-setup.sh` and the
server image built (`docker compose build server`).
"""

import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as aioredis

from floppy_server.infra.juicefs import (
    FormatError,
    JuiceFSFormatter,
    S3Credentials,
    VolumeLocation,
    error_of,
    meta_url,
)
from floppy_server.infra.metadata import META_DB
from floppy_server.infra.storage import S3StorageProber
from tests.conftest import META_POOL

IMAGE = "floppy-server-server"
NETWORK = "floppy-server_default"
GARAGE_HOST = os.environ.get("FLOPPY_TEST_S3_URL", "http://127.0.0.1:4509")
GARAGE_IN_DOCKER = os.environ.get("FLOPPY_TEST_S3_URL_IN_DOCKER", "http://garage:3900")
INSTANCE_HOST = META_POOL.split(",")[0].strip()
INSTANCE_IN_DOCKER = "redis://meta-test-1:6379"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set: run docker/garage-setup.sh")
    return value


def key(prefix: str) -> S3Credentials:
    return S3Credentials(_env(f"{prefix}_ACCESS_KEY_ID"), _env(f"{prefix}_SECRET_KEY"))


@pytest.fixture(scope="module")
def image() -> str:
    found = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    if found.returncode != 0:
        pytest.skip(f"build the server image first: docker compose build server ({IMAGE})")
    return IMAGE


def formatter(image: str) -> JuiceFSFormatter:
    # Variables are passed by name only: their values never reach a command line.
    names = (
        "ACCESS_KEY",
        "SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "META_PASSWORD",
        "AWS_REGION",
    )
    env_flags = [flag for name in names for flag in ("-e", name)]
    prefix = ["docker", "run", "--rm", "-i", "--network", NETWORK, *env_flags]
    return JuiceFSFormatter([*prefix, "--entrypoint", "juicefs", image], timeout_seconds=90)


@pytest.fixture
async def volume_db() -> AsyncIterator[aioredis.Redis]:
    """A disk's own instance, empty, seen from the host."""
    client = aioredis.Redis.from_url(
        INSTANCE_HOST, password=_env("FLOPPY_VALKEY_ADMIN_PASSWORD"), db=META_DB
    )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def location(name: str) -> VolumeLocation:
    return VolumeLocation(
        meta_url=INSTANCE_IN_DOCKER,
        meta_username=None,
        meta_password=_env("FLOPPY_VALKEY_ADMIN_PASSWORD"),
        volume_name=name,
        s3_endpoint=GARAGE_IN_DOCKER,
        s3_region="garage",
        s3_bucket=_env("FLOPPY_TEST_S3_BUCKET"),
    )


async def setting(client: aioredis.Redis) -> dict[str, object]:
    raw = await client.get("setting")
    assert raw is not None
    value: dict[str, object] = json.loads(raw)
    return value


async def test_format_and_trash_days_on_garage(image: str, volume_db: aioredis.Redis) -> None:
    name = f"test-{uuid.uuid4().hex[:10]}"
    first = key("FLOPPY_TEST_S3")
    jfs = formatter(image)
    volume_uuid = await jfs.format(location(name), first, block_size_kb=1024, trash_days=7)
    format_ = await setting(volume_db)
    assert format_["Name"] == name
    assert format_["UUID"] == str(volume_uuid)
    assert format_["BlockSize"] == 1024
    assert format_["TrashDays"] == 7
    assert format_["AccessKey"] == first.access_key_id
    assert first.secret_key not in json.dumps(format_)  # JuiceFS keeps it encrypted

    await jfs.set_trash_days(location(name), 3)
    assert (await setting(volume_db))["TrashDays"] == 3


async def test_a_format_without_a_key_for_managed_disks(
    image: str, volume_db: aioredis.Redis
) -> None:
    # The control plane's key reaches JuiceFS as AWS_* and is used, not stored: each device
    # mounts with its own (spike/perdisk.py proves the mounts).
    name = f"test-{uuid.uuid4().hex[:10]}"
    own = key("FLOPPY_TEST_S3")
    jfs = formatter(image)
    volume_uuid = await jfs.format(
        location(name), own, block_size_kb=1024, trash_days=7, store_key=False
    )
    format_ = await setting(volume_db)
    assert format_["UUID"] == str(volume_uuid)
    assert format_.get("AccessKey", "") == ""
    assert own.secret_key not in json.dumps(format_)
    await jfs.set_trash_days(location(name), 2)
    assert (await setting(volume_db))["TrashDays"] == 2


async def test_config_puts_a_new_key_in_the_format(image: str, volume_db: aioredis.Redis) -> None:
    # Needs patch 0003 (spike/juicefs-replica): the key reaches config through the environment.
    name = f"test-{uuid.uuid4().hex[:10]}"
    first, second = key("FLOPPY_TEST_S3"), key("FLOPPY_TEST_S3_SECOND")
    jfs = formatter(image)
    volume_uuid = await jfs.format(location(name), first, block_size_kb=1024, trash_days=7)
    await jfs.set_key(location(name), second)
    rotated = await setting(volume_db)
    assert rotated["AccessKey"] == second.access_key_id
    assert rotated["UUID"] == str(volume_uuid)
    # A key the bucket refuses fails the sanity check and leaves the format as it was.
    denied = key("FLOPPY_TEST_S3_DENIED")
    with pytest.raises(FormatError) as failure:
        await jfs.set_key(location(name), denied)
    assert denied.secret_key not in str(failure.value)
    assert (await setting(volume_db))["AccessKey"] == second.access_key_id


async def test_format_with_a_refused_key_fails_without_the_secret(
    image: str, volume_db: aioredis.Redis
) -> None:
    denied = key("FLOPPY_TEST_S3_DENIED")
    with pytest.raises(FormatError) as failure:
        await formatter(image).format(
            location(f"test-{uuid.uuid4().hex[:10]}"), denied, block_size_kb=1024, trash_days=7
        )
    message = str(failure.value)
    assert message
    assert denied.secret_key not in message
    assert await volume_db.get("setting") is None


async def test_secrets_go_through_the_environment_not_the_arguments() -> None:
    script = "import json, os, sys; print(json.dumps({'argv': sys.argv, 'env': dict(os.environ)}))"
    jfs = JuiceFSFormatter([sys.executable, "-c", script])
    output = await jfs._run(["config", "redis://meta-1:6379/1"], {"SECRET_KEY": "s3cr3t-value"})
    seen = json.loads(output)
    assert "s3cr3t-value" not in json.dumps(seen["argv"])
    assert seen["env"]["SECRET_KEY"] == "s3cr3t-value"
    assert "FLOPPY_SECRET_KEY" not in seen["env"]
    assert "FLOPPY_DB_APP_PASSWORD" not in seen["env"]


def test_meta_url_carries_the_user_and_database_not_the_password() -> None:
    loc = VolumeLocation(
        meta_url="rediss://old:pw@meta.test:6380/0",
        meta_username="admin",
        meta_password="pw",
        meta_db=4,
        volume_name="v",
        s3_endpoint="https://s3.test/",
        s3_region="",
        s3_bucket="b",
    )
    assert meta_url(loc) == "rediss://admin@meta.test:6380/4"


def test_juicefs_errors_are_reduced_to_the_last_one_and_masked() -> None:
    stderr = (
        "2026/10/17 juicefs[1] <INFO>: Meta address: redis://meta-1:6379/1\n"
        "2026/10/17 juicefs[1] <FATAL>: Storage s3 is not configured: AccessDenied key=SECRET\x1b[0m\n"
    )
    assert error_of(stderr, ["SECRET"]) == "Storage s3 is not configured: AccessDenied key=***"


async def test_the_probe_writes_and_removes_with_an_allowed_key() -> None:
    prober = S3StorageProber(timeout_seconds=5)
    bucket = _env("FLOPPY_TEST_S3_BUCKET")
    ok = await prober.probe(
        endpoint=GARAGE_HOST, region="garage", bucket=bucket, key=key("FLOPPY_TEST_S3"), label="t"
    )
    assert ok.ok


async def test_the_probe_reports_the_providers_answer() -> None:
    prober = S3StorageProber(timeout_seconds=5)
    bucket = _env("FLOPPY_TEST_S3_BUCKET")
    denied = await prober.probe(
        endpoint=GARAGE_HOST,
        region="garage",
        bucket=bucket,
        key=key("FLOPPY_TEST_S3_DENIED"),
        label="t",
    )
    assert (denied.ok, denied.code) == (False, "AccessDenied")
    assert denied.request is not None
    assert denied.request.startswith(f"PUT /{bucket}/floppy-probe-t-")
    missing = await prober.probe(
        endpoint=GARAGE_HOST,
        region="garage",
        bucket="no-such-bucket-floppy",
        key=key("FLOPPY_TEST_S3"),
        label="t",
    )
    assert (missing.ok, missing.code) == (False, "NoSuchBucket")
    wrong_secret = await prober.probe(
        endpoint=GARAGE_HOST,
        region="garage",
        bucket=bucket,
        key=S3Credentials(key("FLOPPY_TEST_S3").access_key_id, "not-the-secret"),
        label="t",
    )
    # Garage answers AccessDenied where AWS says SignatureDoesNotMatch.
    assert not wrong_secret.ok
    assert wrong_secret.code in {"AccessDenied", "SignatureDoesNotMatch"}
    nobody = await prober.probe(
        endpoint="http://127.0.0.1:9",
        region="garage",
        bucket=bucket,
        key=key("FLOPPY_TEST_S3"),
        label="t",
    )
    assert (nobody.ok, nobody.code) == (False, "EndpointConnectionError")
