"""
`GarageAdapter` against a real Garage (the Compose file's, profile s3, or the one
FLOPPY_TEST_GARAGE_ADMIN_URL and FLOPPY_TEST_S3_URL name): a bucket per disk, a key per device
granted read or read-write per bucket and changed in place, deleted with its grants.
"""

import os
import secrets
import uuid
from collections.abc import AsyncIterator

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError

from floppy_server.infra.juicefs import S3Credentials
from floppy_server.infra.providers import GarageAdapter, ProviderError, ProviderKey
from floppy_server.infra.storage import S3StorageProber

ADMIN_URL = os.environ.get("FLOPPY_TEST_GARAGE_ADMIN_URL", "http://127.0.0.1:4540")
S3_URL = os.environ.get("FLOPPY_TEST_S3_URL", "http://127.0.0.1:4509")


@pytest.fixture
async def garage() -> AsyncIterator[GarageAdapter]:
    """The adapter; the test buckets and keys it made are removed afterwards."""
    token = os.environ.get("FLOPPY_GARAGE_ADMIN_TOKEN")
    if not token:
        pytest.skip("FLOPPY_GARAGE_ADMIN_TOKEN is not set: run docker/garage-setup.sh")
    yield GarageAdapter(ADMIN_URL, token, timeout_seconds=5)
    await cleanup(token)


async def cleanup(token: str) -> None:
    """Deletes the `floppy-test-*` keys and buckets (emptied first) left on Garage."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=ADMIN_URL, headers=headers) as client:
        for key in (await client.get("/v2/ListKeys")).json():
            if str(key.get("name", "")).startswith("floppy-test-"):
                await client.post("/v2/DeleteKey", params={"id": key["id"]})
        for bucket in (await client.get("/v2/ListBuckets")).json():
            if any(a.startswith("floppy-test-") for a in bucket.get("globalAliases", [])):
                await client.post("/v2/DeleteBucket", params={"id": bucket["id"]})


def creds(key: ProviderKey) -> S3Credentials:
    return S3Credentials(key.access_key_id, key.secret_key)


async def probe(bucket: str, key: ProviderKey) -> tuple[bool, str | None]:
    result = await S3StorageProber(timeout_seconds=5).probe(
        endpoint=S3_URL, region="garage", bucket=bucket, key=creds(key), label="garage"
    )
    return result.ok, result.code


async def lists(bucket: str, key: ProviderKey) -> bool:
    """Whether the key may list the bucket (a read)."""
    client = boto3.session.Session().client(
        "s3",
        endpoint_url=S3_URL,
        region_name="garage",
        aws_access_key_id=key.access_key_id,
        aws_secret_access_key=key.secret_key,
    )
    try:
        client.list_objects_v2(Bucket=bucket)
    except ClientError:
        return False
    finally:
        client.close()
    return True


def name() -> str:
    return f"floppy-test-{uuid.uuid4().hex[:10]}"


async def test_a_key_is_granted_per_bucket_and_changed_in_place(garage: GarageAdapter) -> None:
    progetti, archivio, riservato = name(), name(), name()
    for bucket in (progetti, archivio, riservato):
        await garage.ensure_bucket(bucket)
    await garage.ensure_bucket(progetti)  # idempotent
    owner = await garage.create_key(f"floppy-test-cp-{secrets.token_hex(3)}")
    device = await garage.create_key(f"floppy-test-dev-{secrets.token_hex(3)}")
    for bucket in (progetti, archivio, riservato):
        await garage.set_grant(owner.provider_ref, bucket, "write", owner=True)
    # A new key reaches nothing.
    assert await probe(progetti, device) == (False, "AccessDenied")
    await garage.set_grant(device.provider_ref, progetti, "write")
    await garage.set_grant(device.provider_ref, archivio, "read")
    assert await probe(progetti, device) == (True, None)
    assert await lists(archivio, device)
    assert (await probe(archivio, device))[1] == "AccessDenied"  # read-only: no put
    assert not await lists(riservato, device)
    # Changed in place: the same key, read-write on Archivio, nothing on Progetti.
    await garage.set_grant(device.provider_ref, archivio, "write")
    await garage.set_grant(device.provider_ref, progetti, "none")
    assert await probe(archivio, device) == (True, None)
    assert not await lists(progetti, device)
    # The owner key formats and probes every bucket.
    for bucket in (progetti, archivio, riservato):
        assert await probe(bucket, owner) == (True, None)
    await garage.delete_key(device.provider_ref)
    await garage.delete_key(device.provider_ref)  # already gone: not an error
    assert not (await probe(archivio, device))[0]
    await garage.delete_key(owner.provider_ref)


async def test_errors_name_the_step_and_never_the_token(garage: GarageAdapter) -> None:
    wrong = GarageAdapter(ADMIN_URL, "not-the-token", timeout_seconds=5)
    with pytest.raises(ProviderError) as failure:
        await wrong.ensure_bucket("floppy-test-nope")
    assert "not-the-token" not in str(failure.value)
    key = await garage.create_key(f"floppy-test-x-{secrets.token_hex(3)}")
    with pytest.raises(ProviderError, match="find the bucket"):
        await garage.set_grant(key.provider_ref, "no-such-bucket-floppy", "read")
    with pytest.raises(ProviderError, match="unreachable"):
        await GarageAdapter("http://127.0.0.1:9", "t", timeout_seconds=2).ensure_bucket("x")


async def test_the_probe_reads_the_buckets_versioning(garage: GarageAdapter) -> None:
    bucket = name()
    await garage.ensure_bucket(bucket)
    key = await garage.create_key(f"floppy-test-v-{secrets.token_hex(3)}")
    await garage.set_grant(key.provider_ref, bucket, "write")
    result = await S3StorageProber(timeout_seconds=5).probe(
        endpoint=S3_URL, region="garage", bucket=bucket, key=creds(key), label="v"
    )
    assert result.ok
    # Garage has no versioning: the answer is off or unknown, never an error.
    assert result.versioning in {"off", "unknown"}
    assert result.old_versions_days is None
