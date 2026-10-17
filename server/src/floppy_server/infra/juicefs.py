"""
Volume formats: `juicefs format` for a new disk and `juicefs config` for a new key or trash.

The format lives in the volume's Valkey database and holds the S3 key that Macs read from
their replica (DECISIONS #4). Phase 1 ships only `FakeVolumeFormatter`; the real one runs the
pinned JuiceFS binary with the key passed through the environment, never on a command line.
"""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4


class FormatError(Exception):
    """JuiceFS refused or failed; the message never contains the key."""


@dataclass(frozen=True)
class VolumeLocation:
    """Where a volume's format lives and where its objects go."""

    meta_url: str
    meta_username: str | None
    meta_password: str | None
    meta_db: int
    volume_name: str
    s3_endpoint: str
    s3_region: str
    s3_bucket: str


@dataclass(frozen=True)
class S3Credentials:
    """An S3 key pair. The secret is never logged or put on a command line."""

    access_key_id: str
    secret_key: str

    def __repr__(self) -> str:
        return f"S3Credentials(access_key_id=...{self.access_key_id[-5:]})"


class VolumeFormatter(Protocol):
    """The JuiceFS operations on a volume's format."""

    async def format(
        self, location: VolumeLocation, key: S3Credentials, *, block_size_kb: int, trash_days: int
    ) -> UUID:
        """Formats a new volume and returns its JuiceFS UUID."""
        ...

    async def set_key(self, location: VolumeLocation, key: S3Credentials) -> None:
        """Puts a new S3 key in the format (`juicefs config --access-key --secret-key`)."""
        ...

    async def set_trash_days(self, location: VolumeLocation, days: int) -> None:
        """Changes the trash retention (`juicefs config --trash-days`)."""
        ...


@dataclass
class FakeVolumeFormatter:
    """
    In-memory `VolumeFormatter`. Volumes whose name is in `fail_format`, and any call while
    `fail_config` holds the volume name, fail like JuiceFS would. In development, a volume
    name starting with `fail-` fails its format, to show the error state.
    """

    formats: dict[tuple[str, int], dict[str, object]] = field(default_factory=dict)
    fail_format: set[str] = field(default_factory=set)
    fail_config: set[str] = field(default_factory=set)

    async def format(
        self, location: VolumeLocation, key: S3Credentials, *, block_size_kb: int, trash_days: int
    ) -> UUID:
        if location.volume_name in self.fail_format or location.volume_name.startswith("fail-"):
            raise FormatError("juicefs format: storage is not accessible")
        slot = (location.meta_url, location.meta_db)
        if slot in self.formats:
            raise FormatError("juicefs format: the database already holds a volume")
        volume_uuid = uuid4()
        self.formats[slot] = {
            "name": location.volume_name,
            "uuid": volume_uuid,
            "access_key_id": key.access_key_id,
            "block_size_kb": block_size_kb,
            "trash_days": trash_days,
        }
        return volume_uuid

    async def set_key(self, location: VolumeLocation, key: S3Credentials) -> None:
        if location.volume_name in self.fail_config:
            raise FormatError("juicefs config: database is not reachable")
        self.formats.setdefault((location.meta_url, location.meta_db), {})["access_key_id"] = (
            key.access_key_id
        )

    async def set_trash_days(self, location: VolumeLocation, days: int) -> None:
        if location.volume_name in self.fail_config:
            raise FormatError("juicefs config: database is not reachable")
        self.formats.setdefault((location.meta_url, location.meta_db), {})["trash_days"] = days
