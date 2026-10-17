"""
A disk's Valkey instance as the control plane sees it: the devices' ACL users with the ACL of
their access, health and counters.

The control plane talks to an instance only to create, change and delete ACL users, read the
counters it shows and ping it (docs/DATABASE_SCHEMA.md, "What lives where"); it never proxies
metadata. `ValkeyMetadataAdmin` does it for real; `FakeMetadataAdmin` keeps the same state in
memory for tests and development without Valkey.
"""

import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ResponseError

DEVICE_USER_PREFIX = "floppy_d_"
# A disk's metadata lives in database 1 of its own instance; database 0 stays empty, the one
# tools hit by accident.
META_DB = 1

Access = Literal["read", "write"]

# The ACL of each access, exactly as spike/perdisk.py verified them: what the master's
# commandstats showed for a full workload (mount, SMB browsing, writes, renames, xattrs,
# heartbeats, unmount), with an empty ACL LOG. Order matters: -@dangerous drops KEYS, FLUSHDB,
# INFO, PSYNC and REPLCONF, and the needed ones come back after it. Replication needs PSYNC,
# REPLCONF and PING; the patched client's probe reads INFO replication; JuiceFS sends CLUSTER
# INFO once to detect a cluster. Writes add the commands, MULTI/EXEC/WATCH and the scripts
# (EVALSHA/EVAL for the optimistic commit and the lookups, SCRIPT LOAD at session start). A
# read-only user that mounts without --read-only fails at session creation (NOPERM).
ACL_READ = (
    "resetkeys", "~*", "resetchannels", "-@all", "+@read", "-@dangerous",
    "+ping", "+select", "+info", "+cluster|info", "+psync", "+replconf",
)  # fmt: skip
ACL_WRITE = (
    "resetkeys", "~*", "resetchannels", "-@all", "+@read", "+@write", "+@transaction",
    "-@dangerous", "+ping", "+select", "+info", "+cluster|info", "+psync", "+replconf",
    "+eval", "+evalsha", "+script|load",
)  # fmt: skip
ACL_OF: dict[str, tuple[str, ...]] = {"read": ACL_READ, "write": ACL_WRITE}


class MetadataUnavailableError(Exception):
    """The instance did not answer, refused the credentials or the command."""


@dataclass(frozen=True)
class ValkeyTarget:
    """How the control plane reaches one disk's instance, with its admin credentials."""

    url: str
    username: str | None
    password: str | None

    def __repr__(self) -> str:
        return f"ValkeyTarget(url={self.url!r})"


@dataclass(frozen=True)
class DeviceUser:
    """A device's user as the instance has it: the password hashes it accepts and its access."""

    passwords: frozenset[bytes]
    access: Access | None


@dataclass(frozen=True)
class VolumeCounters:
    """JuiceFS's own counters in a volume's database."""

    used_bytes: int
    file_count: int


class MetadataAdmin(Protocol):
    """The operations the control plane runs on a disk's instance."""

    async def ping(self, target: ValkeyTarget) -> float:
        """Round trip of a PING, in milliseconds."""
        ...

    async def set_device_user(
        self, target: ValkeyTarget, username: str, password_sha256: bytes, access: Access
    ) -> None:
        """
        Creates or redefines a device's user with this password hash and the ACL of `access`.
        Open connections follow the new ACL at once; they are closed only when the password
        changed.
        """
        ...

    async def delete_device_user(self, target: ValkeyTarget, username: str) -> None:
        """Deletes a device's user and kills its connections; a missing user is not an error."""
        ...

    async def device_users(self, target: ValkeyTarget) -> dict[str, DeviceUser]:
        """Every device user on the instance."""
        ...

    async def volume_counters(self, target: ValkeyTarget) -> VolumeCounters:
        """Used space and inode count of the disk, in database `META_DB`."""
        ...


class ValkeyMetadataAdmin:
    """`MetadataAdmin` over a real Valkey (or Redis-compatible) server."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._timeout = timeout_seconds

    def _client(self, target: ValkeyTarget, db: int = 0) -> aioredis.Redis:
        return aioredis.Redis.from_url(
            target.url,
            username=target.username,
            password=target.password,
            db=db,
            socket_timeout=self._timeout,
            socket_connect_timeout=self._timeout,
            decode_responses=False,
        )

    async def ping(self, target: ValkeyTarget) -> float:
        client = self._client(target)
        try:
            start = time.perf_counter()
            await client.ping()
            return (time.perf_counter() - start) * 1000
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def set_device_user(
        self, target: ValkeyTarget, username: str, password_sha256: bytes, access: Access
    ) -> None:
        _check_username(username)
        client = self._client(target)
        try:
            before = await _get_user(client, username)
            await client.execute_command(
                "ACL",
                "SETUSER",
                username,
                "reset",
                "on",
                f"#{password_sha256.hex()}",
                *ACL_OF[access],
            )
            # Valkey checks the ACL on every command, so a changed access applies to open
            # connections as they are; a new password must close the ones opened with the old.
            if before is not None and password_sha256 not in before.passwords:
                await _kill_user_clients(client, username)
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def delete_device_user(self, target: ValkeyTarget, username: str) -> None:
        _check_username(username)
        client = self._client(target)
        try:
            # DELUSER first, so the device cannot authenticate again, then close what is open.
            await client.execute_command("ACL", "DELUSER", username)
            await _kill_user_clients(client, username)
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def device_users(self, target: ValkeyTarget) -> dict[str, DeviceUser]:
        client = self._client(target)
        try:
            names = [n.decode() for n in await client.execute_command("ACL", "USERS")]
            users: dict[str, DeviceUser] = {}
            for name in names:
                if not name.startswith(DEVICE_USER_PREFIX):
                    continue
                user = await _get_user(client, name)
                if user is not None:
                    users[name] = user
            return users
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def volume_counters(self, target: ValkeyTarget) -> VolumeCounters:
        client = self._client(target, db=META_DB)
        try:
            used, inodes = await client.mget("usedSpace", "totalInodes")
            return VolumeCounters(used_bytes=int(used or 0), file_count=int(inodes or 0))
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()


async def _get_user(client: aioredis.Redis, username: str) -> DeviceUser | None:
    info = await client.execute_command("ACL", "GETUSER", username)
    if info is None:
        return None
    fields = _pairs(info)
    raw = fields.get(b"passwords", [])
    passwords = (
        frozenset(bytes.fromhex(p.decode()) for p in raw if isinstance(p, bytes))
        if isinstance(raw, list)
        else frozenset()
    )
    commands = fields.get(b"commands", b"")
    return DeviceUser(passwords=passwords, access=access_of(commands))


def access_of(commands: object) -> Access | None:
    """The access an `ACL GETUSER` command list grants: write, read, or neither of ours."""
    text = commands.decode() if isinstance(commands, bytes) else str(commands)
    rules = set(text.split())
    if "+@write" in rules and "+@read" in rules:
        return "write"
    if "+@read" in rules and "+@write" not in rules and "-@all" in rules:
        return "read"
    return None


def _pairs(info: object) -> dict[bytes, object]:
    """An `ACL GETUSER` answer as a map (a flat list or a map, by protocol)."""
    if isinstance(info, dict):
        return {k if isinstance(k, bytes) else str(k).encode(): v for k, v in info.items()}
    if isinstance(info, list):
        return {
            info[i]: info[i + 1] for i in range(0, len(info) - 1, 2) if isinstance(info[i], bytes)
        }
    return {}


async def _kill_user_clients(client: aioredis.Redis, username: str) -> None:
    try:
        await client.execute_command("CLIENT", "KILL", "USER", username)
    except ResponseError as error:
        # Valkey answers "No such user" once the user is gone; its connections were closed
        # by DELUSER itself, so there is nothing left to kill.
        if "no such user" not in str(error).lower():
            raise


def _check_username(username: str) -> None:
    # The username is generated from the device id, but it reaches a command line: refuse
    # anything that is not the generated shape.
    rest = username.removeprefix(DEVICE_USER_PREFIX)
    if rest == username or len(rest) != 32 or any(c not in "0123456789abcdef" for c in rest):
        raise ValueError(f"not a device user name: {username!r}")


@dataclass
class FakeMetadataAdmin:
    """
    In-memory `MetadataAdmin`: users (password hash and access) and counters per instance
    URL; `down` holds the URLs that fail like an instance that does not answer.
    """

    users: dict[str, dict[str, tuple[bytes, Access]]] = field(default_factory=dict)
    counters: dict[str, VolumeCounters] = field(default_factory=dict)
    killed: list[str] = field(default_factory=list)
    down: set[str] = field(default_factory=set)

    def _guard(self, target: ValkeyTarget) -> None:
        if target.url in self.down or "*" in self.down:
            raise MetadataUnavailableError("ConnectionError")

    async def ping(self, target: ValkeyTarget) -> float:
        self._guard(target)
        return 1.0

    async def set_device_user(
        self, target: ValkeyTarget, username: str, password_sha256: bytes, access: Access
    ) -> None:
        self._guard(target)
        _check_username(username)
        before = self.users.setdefault(target.url, {}).get(username)
        self.users[target.url][username] = (password_sha256, access)
        if before is not None and before[0] != password_sha256:
            self.killed.append(username)

    async def delete_device_user(self, target: ValkeyTarget, username: str) -> None:
        self._guard(target)
        _check_username(username)
        self.users.setdefault(target.url, {}).pop(username, None)
        self.killed.append(username)

    async def device_users(self, target: ValkeyTarget) -> dict[str, DeviceUser]:
        self._guard(target)
        return {
            name: DeviceUser(passwords=frozenset({digest}), access=access)
            for name, (digest, access) in self.users.get(target.url, {}).items()
        }

    async def volume_counters(self, target: ValkeyTarget) -> VolumeCounters:
        self._guard(target)
        return self.counters.get(target.url, VolumeCounters(0, 0))

    def access(self, url: str, username: str) -> Access | None:
        """The access of a user on an instance, or None when it has no user there."""
        entry = self.users.get(url, {}).get(username)
        return entry[1] if entry else None
