"""
The storage probe: a test write with a key, before the control plane trusts it.

Used when the bootstrap registers the first key, when a rotation starts and when an admin
checks the storage settings. Phase 1 ships only `FakeStorageProber`; the real one writes and
removes a small object with plain S3 (DECISIONS #4: no provider API).
"""

from dataclasses import dataclass, field
from typing import Protocol

from floppy_server.infra.juicefs import S3Credentials


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
