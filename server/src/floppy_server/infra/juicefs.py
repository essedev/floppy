"""
Volume formats: `juicefs format` for a new disk and `juicefs config` for a new key or trash.

The format lives in database 1 of the disk's own Valkey instance. In manual mode it holds the
disk's S3 key, which devices read from their replica; in managed mode it holds none and each
mount brings its own (DECISIONS #24). `JuiceFSFormatter` runs the patched JuiceFS binary of
the server image with the key passed through the environment, never on a command line;
`FakeVolumeFormatter` keeps the same state in memory for tests and development.
"""

import asyncio
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

from floppy_server.infra.metadata import META_DB


class FormatError(Exception):
    """JuiceFS refused or failed; the message never contains the key."""


@dataclass(frozen=True)
class VolumeLocation:
    """Where a volume's format lives and where its objects go."""

    meta_url: str
    meta_username: str | None
    meta_password: str | None
    volume_name: str
    s3_endpoint: str
    s3_region: str
    s3_bucket: str
    meta_db: int = META_DB

    def __repr__(self) -> str:
        return f"VolumeLocation(meta_url={self.meta_url!r}, volume_name={self.volume_name!r})"


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
        self,
        location: VolumeLocation,
        key: S3Credentials,
        *,
        block_size_kb: int,
        trash_days: int,
        store_key: bool = True,
    ) -> UUID:
        """
        Formats a new volume and returns its JuiceFS UUID. With `store_key` the key goes into
        the format (manual: the disk's key); without, the format holds no key and every mount
        brings its own (managed, DATABASE_SCHEMA.md "Storage modes").
        """
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
        self,
        location: VolumeLocation,
        key: S3Credentials,
        *,
        block_size_kb: int,
        trash_days: int,
        store_key: bool = True,
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
            "access_key_id": key.access_key_id if store_key else "",
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


# Environment the JuiceFS process inherits: enough to run it (or `docker` in front of it in
# the tests), and nothing else of the control plane's own environment.
PASSTHROUGH_ENV = ("PATH", "HOME", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
JUICEFS_LOG_LEVEL = re.compile(r"<(FATAL|ERROR|WARNING)>:\s*(.*)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def meta_url(location: VolumeLocation) -> str:
    """The volume's metadata URL for JuiceFS: user and database, the password stays out."""
    parts = urlsplit(location.meta_url)
    hostport = parts.netloc.rsplit("@", 1)[-1]
    user = f"{quote(location.meta_username, safe='')}@" if location.meta_username else ""
    return urlunsplit((parts.scheme, f"{user}{hostport}", f"/{location.meta_db}", "", ""))


def bucket_url(location: VolumeLocation) -> str:
    """The bucket as JuiceFS takes it, path style: `<endpoint>/<bucket>`."""
    return f"{location.s3_endpoint.rstrip('/')}/{location.s3_bucket}"


def error_of(stderr: str, secrets: Sequence[str]) -> str:
    """JuiceFS's last error line, with every secret masked."""
    lines = [ANSI.sub("", line) for line in stderr.splitlines() if line.strip()]
    found = [m.group(2) for line in lines if (m := JUICEFS_LOG_LEVEL.search(line))]
    message = (found or lines or ["juicefs failed without a message"])[-1]
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message[:300]


class JuiceFSFormatter:
    """
    `VolumeFormatter` over the JuiceFS command. The S3 key and the Valkey password reach the
    process only through its environment (`ACCESS_KEY`, `SECRET_KEY`, `META_PASSWORD`), never
    its arguments, which `ps` shows. `command` is the binary, or a prefix that runs it
    elsewhere (the tests run it in the server image with `docker run`).
    """

    def __init__(
        self, command: Sequence[str] = ("juicefs",), timeout_seconds: float = 120.0
    ) -> None:
        self._command = tuple(command)
        self._timeout = timeout_seconds

    async def _run(self, args: Sequence[str], secrets: dict[str, str]) -> str:
        env = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
        env.update(secrets)
        process = await asyncio.create_subprocess_exec(
            *self._command,
            *args,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self._timeout)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise FormatError(f"juicefs {args[0]} timed out after {self._timeout:.0f} s") from error
        if process.returncode != 0:
            raise FormatError(error_of(stderr.decode(errors="replace"), list(secrets.values())))
        return stdout.decode(errors="replace")

    @staticmethod
    def _secrets(
        location: VolumeLocation, key: S3Credentials | None = None, *, store_key: bool = True
    ) -> dict[str, str]:
        secrets: dict[str, str] = {}
        if location.meta_password:
            secrets["META_PASSWORD"] = location.meta_password
        if key is not None:
            # ACCESS_KEY and SECRET_KEY end up in the format (and, with patch 0003, change it);
            # the AWS SDK's own variables are only used, never stored.
            names = (
                ("ACCESS_KEY", "SECRET_KEY")
                if store_key
                else (
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                )
            )
            secrets[names[0]] = key.access_key_id
            secrets[names[1]] = key.secret_key
            if location.s3_region:
                secrets["AWS_REGION"] = location.s3_region
        return secrets

    async def format(
        self,
        location: VolumeLocation,
        key: S3Credentials,
        *,
        block_size_kb: int,
        trash_days: int,
        store_key: bool = True,
    ) -> UUID:
        await self._run(
            [
                "format",
                "--storage",
                "s3",
                "--bucket",
                bucket_url(location),
                "--block-size",
                f"{block_size_kb}K",
                "--trash-days",
                str(trash_days),
                meta_url(location),
                location.volume_name,
            ],
            self._secrets(location, key, store_key=store_key),
        )
        return await self._uuid(location)

    async def _uuid(self, location: VolumeLocation) -> UUID:
        """The volume's UUID, read back from `juicefs status` (the secret key is not in it)."""
        output = await self._run(["status", meta_url(location)], self._secrets(location))
        try:
            return UUID(json.loads(output)["Setting"]["UUID"])
        except (ValueError, KeyError, TypeError) as error:
            raise FormatError("juicefs status did not report the volume's UUID") from error

    async def set_key(self, location: VolumeLocation, key: S3Credentials) -> None:
        # `config` writes a test object with the new key before saving it (its sanity check).
        await self._run(["config", meta_url(location), "--yes"], self._secrets(location, key))

    async def set_trash_days(self, location: VolumeLocation, days: int) -> None:
        await self._run(
            ["config", meta_url(location), "--trash-days", str(days), "--yes"],
            self._secrets(location),
        )
