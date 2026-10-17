"""
`GarageAdapter` against the Garage of the Compose file (profile s3, admin API on 4540): a
managed organization's bucket and keys, each key working until it is deleted.
"""

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from floppy_server.infra.juicefs import S3Credentials
from floppy_server.infra.providers import GarageAdapter, ProviderError
from floppy_server.infra.storage import S3StorageProber

ADMIN_URL = "http://127.0.0.1:4540"
S3_URL = "http://127.0.0.1:4509"


@pytest.fixture
async def garage() -> AsyncIterator[GarageAdapter]:
    """The adapter; the test buckets it made are removed afterwards (they are empty)."""
    token = os.environ.get("FLOPPY_GARAGE_ADMIN_TOKEN")
    if not token:
        pytest.skip("FLOPPY_GARAGE_ADMIN_TOKEN is not set: run docker/garage-setup.sh")
    yield GarageAdapter(ADMIN_URL, token, timeout_seconds=5)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=ADMIN_URL, headers=headers) as client:
        buckets = (await client.get("/v2/ListBuckets")).json()
        for bucket in buckets:
            if any(a.startswith("floppy-test-") for a in bucket.get("globalAliases", [])):
                await client.post("/v2/DeleteBucket", params={"id": bucket["id"]})


async def probe(bucket: str, key: S3Credentials) -> tuple[bool, str | None]:
    result = await S3StorageProber(timeout_seconds=5).probe(
        endpoint=S3_URL, region="garage", bucket=bucket, key=key, label="garage"
    )
    return result.ok, result.code


async def test_a_bucket_and_a_key_scoped_to_it(garage: GarageAdapter) -> None:
    bucket = f"floppy-test-{uuid.uuid4().hex[:10]}"
    await garage.ensure_bucket(bucket)
    await garage.ensure_bucket(bucket)  # idempotent
    first = await garage.create_key(bucket, "floppy-test-mac-a")
    second = await garage.create_key(bucket, "floppy-test-mac-b")
    try:
        assert await probe(bucket, S3Credentials(first.access_key_id, first.secret_key)) == (
            True,
            None,
        )
        assert await probe(bucket, S3Credentials(second.access_key_id, second.secret_key)) == (
            True,
            None,
        )
        # Scoped: the key reaches its bucket only.
        other = f"floppy-test-{uuid.uuid4().hex[:10]}"
        await garage.ensure_bucket(other)
        ok, code = await probe(other, S3Credentials(first.access_key_id, first.secret_key))
        assert not ok
        assert code == "AccessDenied"

        await garage.delete_key(first.provider_ref)
        await garage.delete_key(first.provider_ref)  # already gone: not an error
        ok, _ = await probe(bucket, S3Credentials(first.access_key_id, first.secret_key))
        assert not ok
        assert await probe(bucket, S3Credentials(second.access_key_id, second.secret_key)) == (
            True,
            None,
        )
    finally:
        await garage.delete_key(second.provider_ref)


async def test_errors_name_the_step_and_never_the_token(garage: GarageAdapter) -> None:
    wrong = GarageAdapter(ADMIN_URL, "not-the-token", timeout_seconds=5)
    with pytest.raises(ProviderError) as failure:
        await wrong.ensure_bucket("floppy-test-nope")
    assert "not-the-token" not in str(failure.value)
    with pytest.raises(ProviderError, match="find the bucket"):
        await garage.create_key("no-such-bucket-floppy", "x")
    with pytest.raises(ProviderError, match="unreachable"):
        await GarageAdapter("http://127.0.0.1:9", "t", timeout_seconds=2).ensure_bucket("x")


async def test_the_probe_reads_the_buckets_versioning(garage: GarageAdapter) -> None:
    bucket = f"floppy-test-{uuid.uuid4().hex[:10]}"
    await garage.ensure_bucket(bucket)
    key = await garage.create_key(bucket, "floppy-test-versioning")
    try:
        result = await S3StorageProber(timeout_seconds=5).probe(
            endpoint=S3_URL,
            region="garage",
            bucket=bucket,
            key=S3Credentials(key.access_key_id, key.secret_key),
            label="v",
        )
        assert result.ok
        # Garage has no versioning: the answer is off or unknown, never an error.
        assert result.versioning in {"off", "unknown"}
        assert result.old_versions_days is None
    finally:
        await garage.delete_key(key.provider_ref)
