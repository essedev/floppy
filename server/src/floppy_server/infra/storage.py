"""
The storage probe: a test write with a key, before the control plane trusts it.

Used when the bootstrap registers the first key, when a rotation starts and when an admin
checks the storage settings. `S3StorageProber` writes and removes a small object with plain
S3, path style (DECISIONS #4: no provider API); `FakeStorageProber` answers from fixed rules.
"""

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from floppy_server.infra.juicefs import S3Credentials

PROBE_BODY = b"floppy storage probe\n"


class S3StorageProber:
    """`StorageProber` over S3: `PUT` then `DELETE` of `floppy-probe-<label>-<random>`."""

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._config = Config(
            s3={"addressing_style": "path"},
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"max_attempts": 2, "mode": "standard"},
        )

    async def probe(
        self, *, endpoint: str, region: str, bucket: str, key: S3Credentials, label: str
    ) -> ProbeResult:
        return await asyncio.to_thread(self._probe, endpoint, region, bucket, key, label)

    def _probe(
        self, endpoint: str, region: str, bucket: str, key: S3Credentials, label: str
    ) -> ProbeResult:
        name = f"floppy-probe-{label}-{secrets.token_hex(4)}"
        client = boto3.session.Session().client(
            "s3",
            endpoint_url=endpoint,
            region_name=region or "us-east-1",
            aws_access_key_id=key.access_key_id,
            aws_secret_access_key=key.secret_key,
            config=self._config,
        )
        step = f"PUT /{bucket}/{name}"
        try:
            client.put_object(Bucket=bucket, Key=name, Body=PROBE_BODY)
            step = f"DELETE /{bucket}/{name}"
            client.delete_object(Bucket=bucket, Key=name)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", "")) or "ClientError"
            return ProbeResult(ok=False, code=code, request=step)
        except BotoCoreError as error:
            # No answer at all: a wrong endpoint, DNS, TLS. The class name says which.
            return ProbeResult(ok=False, code=type(error).__name__, request=step)
        finally:
            client.close()
        return ProbeResult(ok=True)


@dataclass(frozen=True)
class ProbeResult:
    """The probe's outcome; on failure the provider's error code and the request that got it."""

    ok: bool
    code: str | None = None
    request: str | None = None


class StorageProber(Protocol):
    """A test write against a bucket with a key."""

    async def probe(
        self, *, endpoint: str, region: str, bucket: str, key: S3Credentials, label: str
    ) -> ProbeResult:
        """Writes and removes `floppy-probe-<label>` in the bucket."""
        ...


@dataclass
class FakeStorageProber:
    """
    In-memory `StorageProber` with the admin web's fake triggers: an access key containing
    `DENIED` gets `AccessDenied`, a bucket containing `missing` gets `NoSuchBucket`.
    """

    probes: list[tuple[str, str]] = field(default_factory=list)
    deny_keys: set[str] = field(default_factory=set)

    async def probe(
        self, *, endpoint: str, region: str, bucket: str, key: S3Credentials, label: str
    ) -> ProbeResult:
        self.probes.append((bucket, key.access_key_id))
        request = f"PUT /{bucket}/floppy-probe-{label}"
        if "DENIED" in key.access_key_id.upper() or key.access_key_id in self.deny_keys:
            return ProbeResult(ok=False, code="AccessDenied", request=request)
        if "missing" in bucket.lower():
            return ProbeResult(ok=False, code="NoSuchBucket", request=request)
        return ProbeResult(ok=True)
