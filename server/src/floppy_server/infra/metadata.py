"""
The central Valkey as the control plane sees it: ACL users per Mac, health and counters.

The control plane talks to Valkey only to create and delete ACL users, read the counters it
shows and ping it (docs/DATABASE_SCHEMA.md, "What lives where"); it never proxies metadata.
`ValkeyMetadataAdmin` does it for real; `FakeMetadataAdmin` keeps the same state in memory
for tests and development without a Valkey.
"""

import time
from dataclasses import dataclass, field
from typing import Protocol

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ResponseError

DEVICE_USER_PREFIX = "floppy_d_"

# What a Mac's user may do (DECISIONS #7): JuiceFS's metadata reads and writes (optimistic
# writes through EVAL/EVALSHA, read-your-writes counters with HINCRBY), and replication for
# its replica (PSYNC, REPLCONF, PING). Administrative commands stay closed, and so do the
# commands that would wipe every disk at once. Proven in spike/acceptance.py.
DEVICE_USER_RULES = (
    "~*",
    "&*",
    "+@all",
    "-@admin",
    "+psync",
    "+replconf",
    "+ping",
    "+info",
    "+config|get",
    "+client|setname",
    "+client|id",
    "-flushall",
    "-flushdb",
    "-swapdb",
)


class MetadataUnavailableError(Exception):
    """Valkey did not answer, refused the credentials or the command."""


@dataclass(frozen=True)
class ValkeyTarget:
    """How the control plane reaches one organization's Valkey, with admin credentials."""

    url: str
    username: str | None
    password: str | None


@dataclass(frozen=True)
class VolumeCounters:
    """JuiceFS's own counters in a volume's database."""

    used_bytes: int
    file_count: int


class MetadataAdmin(Protocol):
    """The operations the control plane runs on an organization's Valkey."""

    async def ping(self, target: ValkeyTarget) -> float:
        """Round trip of a PING, in milliseconds."""
        ...

    async def set_device_user(
        self, target: ValkeyTarget, username: str, password_sha256: bytes
    ) -> None:
        """Creates or redefines a Mac's user with this password hash, closing its old connections."""
        ...

    async def delete_device_user(self, target: ValkeyTarget, username: str) -> None:
        """Deletes a Mac's user and kills its connections; a missing user is not an error."""
        ...

    async def device_users(self, target: ValkeyTarget) -> dict[str, set[bytes]]:
        """Every Mac user on the instance, with the password hashes it accepts."""
        ...

    async def volume_counters(self, target: ValkeyTarget, meta_db: int) -> VolumeCounters:
        """Used space and inode count of the volume in database `meta_db`."""
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
        self, target: ValkeyTarget, username: str, password_sha256: bytes
    ) -> None:
        _check_username(username)
        client = self._client(target)
        try:
            await client.execute_command(
                "ACL",
                "SETUSER",
                username,
                "reset",
                "on",
                f"#{password_sha256.hex()}",
                *DEVICE_USER_RULES,
            )
            await _kill_user_clients(client, username)
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def delete_device_user(self, target: ValkeyTarget, username: str) -> None:
        _check_username(username)
        client = self._client(target)
        try:
            # DELUSER first, so the Mac cannot authenticate again, then close what is open.
            await client.execute_command("ACL", "DELUSER", username)
            await _kill_user_clients(client, username)
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def device_users(self, target: ValkeyTarget) -> dict[str, set[bytes]]:
        client = self._client(target)
        try:
            names = [n.decode() for n in await client.execute_command("ACL", "USERS")]
            users: dict[str, set[bytes]] = {}
            for name in names:
                if not name.startswith(DEVICE_USER_PREFIX):
                    continue
                info = await client.execute_command("ACL", "GETUSER", name)
                users[name] = _passwords_of(info)
            return users
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()

    async def volume_counters(self, target: ValkeyTarget, meta_db: int) -> VolumeCounters:
        client = self._client(target, db=meta_db)
        try:
            used, inodes = await client.mget("usedSpace", "totalInodes")
            return VolumeCounters(used_bytes=int(used or 0), file_count=int(inodes or 0))
        except RedisError as error:
            raise MetadataUnavailableError(type(error).__name__) from error
        finally:
            await client.aclose()


async def _kill_user_clients(client: aioredis.Redis, username: str) -> None:
    try:
        await client.execute_command("CLIENT", "KILL", "USER", username)
    except ResponseError as error:
        # Valkey answers "No such user" once the user is gone; its connections were closed
        # by DELUSER itself, so there is nothing left to kill.
        if "no such user" not in str(error).lower():
            raise


def _passwords_of(info: object) -> set[bytes]:
    """The password hashes in an `ACL GETUSER` answer (a flat list or a map, by protocol)."""
    pairs: dict[bytes, object]
    if isinstance(info, dict):
        pairs = {k if isinstance(k, bytes) else str(k).encode(): v for k, v in info.items()}
    elif isinstance(info, list):
        pairs = {
            info[i]: info[i + 1] for i in range(0, len(info) - 1, 2) if isinstance(info[i], bytes)
        }
    else:
        return set()
    raw = pairs.get(b"passwords", [])
    if not isinstance(raw, list):
        return set()
    return {bytes.fromhex(p.decode()) for p in raw if isinstance(p, bytes)}


def _check_username(username: str) -> None:
    # The username is generated from the device id, but it reaches a command line: refuse
    # anything that is not the generated shape.
    rest = username.removeprefix(DEVICE_USER_PREFIX)
    if rest == username or len(rest) != 32 or any(c not in "0123456789abcdef" for c in rest):
        raise ValueError(f"not a device user name: {username!r}")


@dataclass
class FakeMetadataAdmin:
    """In-memory `MetadataAdmin`: users and counters per target URL; `down` makes it fail."""

    users: dict[str, dict[str, bytes]] = field(default_factory=dict)
    counters: dict[tuple[str, int], VolumeCounters] = field(default_factory=dict)
    killed: list[str] = field(default_factory=list)
    down: bool = False

    def _guard(self) -> None:
        if self.down:
            raise MetadataUnavailableError("ConnectionError")

    async def ping(self, target: ValkeyTarget) -> float:
        self._guard()
        return 1.0

    async def set_device_user(
        self, target: ValkeyTarget, username: str, password_sha256: bytes
    ) -> None:
        self._guard()
        _check_username(username)
        self.users.setdefault(target.url, {})[username] = password_sha256
        self.killed.append(username)

    async def delete_device_user(self, target: ValkeyTarget, username: str) -> None:
        self._guard()
        _check_username(username)
        self.users.setdefault(target.url, {}).pop(username, None)
        self.killed.append(username)

    async def device_users(self, target: ValkeyTarget) -> dict[str, set[bytes]]:
        self._guard()
        return {name: {digest} for name, digest in self.users.get(target.url, {}).items()}

    async def volume_counters(self, target: ValkeyTarget, meta_db: int) -> VolumeCounters:
        self._guard()
        return self.counters.get((target.url, meta_db), VolumeCounters(0, 0))
