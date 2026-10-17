"""
The meta service: who gives each disk its own Valkey instance (DECISIONS #23, #25).

The control plane never starts containers; it asks a `MetaService` for an instance by name
(the volume's id) and gets its address and admin credentials back. Creating is idempotent by
name, so a job interrupted between the call and its commit finds the same instance again.

`PoolMetaService` hands out instances from a fixed list, the Compose file's `meta-*` services
in development and the tests: an instance is taken by writing the disk's name into a key of
its database 0, and one that is taken or holds any data is never handed out. The real meta
service of DECISIONS #25 (one Valkey per disk behind the TLS proxy) implements the same
interface and is not built. `FakeMetaService` invents addresses for tests with a fake Valkey.
"""

from dataclasses import dataclass, field
from typing import Protocol

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from floppy_server.infra.metadata import META_DB

CLAIM_KEY = "floppy:instance"


class MetaServiceError(Exception):
    """The meta service could not give or release an instance; never contains a secret."""


@dataclass(frozen=True)
class InstanceSpec:
    """A disk's instance: devices' address, the control plane's, and admin credentials."""

    service_ref: str
    url: str
    admin_url: str | None
    admin_username: str | None
    admin_password: str | None

    def __repr__(self) -> str:
        return f"InstanceSpec(service_ref={self.service_ref!r}, url={self.url!r})"


class MetaService(Protocol):
    """Creates and releases the disks' Valkey instances."""

    async def create_instance(self, name: str) -> InstanceSpec:
        """The instance for `name`: the one it already has, or a new empty one."""
        ...

    async def release_instance(self, service_ref: str, *, discard: bool) -> None:
        """
        Stops serving an instance. Its data stays unless `discard`, which wipes it (only after
        a creation that failed, whose format never reached a device).
        """
        ...


@dataclass(frozen=True)
class PoolEntry:
    """One instance of the pool: how devices reach it and how the control plane does."""

    url: str
    admin_url: str


def parse_pool(raw: str) -> list[PoolEntry]:
    """
    `FLOPPY_META_POOL`: comma-separated `url` or `url|admin_url` (the control plane's own
    route when it differs, a Compose name). No credentials in either.
    """
    entries: list[PoolEntry] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        url, _, admin = item.partition("|")
        if "@" in item:
            raise ValueError("FLOPPY_META_POOL carries no credentials")
        entries.append(PoolEntry(url=url.strip(), admin_url=(admin or url).strip()))
    return entries


class PoolMetaService:
    """`MetaService` over a fixed pool of Valkey instances sharing one admin password."""

    def __init__(
        self,
        entries: list[PoolEntry],
        admin_password: str | None,
        admin_username: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._entries = entries
        self._password = admin_password
        self._username = admin_username
        self._timeout = timeout_seconds

    def _client(self, entry: PoolEntry, db: int = 0) -> aioredis.Redis:
        return aioredis.Redis.from_url(
            entry.admin_url,
            username=self._username,
            password=self._password,
            db=db,
            socket_timeout=self._timeout,
            socket_connect_timeout=self._timeout,
        )

    def _spec(self, entry: PoolEntry) -> InstanceSpec:
        return InstanceSpec(
            service_ref=entry.url,
            url=entry.url,
            admin_url=entry.admin_url if entry.admin_url != entry.url else None,
            admin_username=self._username,
            admin_password=self._password,
        )

    async def create_instance(self, name: str) -> InstanceSpec:
        free: list[PoolEntry] = []
        for entry in self._entries:
            client = self._client(entry)
            try:
                claim = await client.get(CLAIM_KEY)
                if claim is not None and _text(claim) == name:
                    return self._spec(entry)
                if claim is None and not await _holds_data(client):
                    free.append(entry)
            except RedisError:
                continue  # an instance that does not answer is not handed out
            finally:
                await client.aclose()
        for entry in free:
            client = self._client(entry)
            try:
                # SET NX: two control planes asking at once never get the same instance.
                if await client.set(CLAIM_KEY, name, nx=True):
                    return self._spec(entry)
            except RedisError:
                continue
            finally:
                await client.aclose()
        raise MetaServiceError("no free instance in the pool (FLOPPY_META_POOL)")

    async def release_instance(self, service_ref: str, *, discard: bool) -> None:
        entry = next((e for e in self._entries if e.url == service_ref), None)
        if entry is None:
            raise MetaServiceError("the instance is not in the pool")
        if not discard:
            return  # its data and its claim stay: the instance is never handed out again
        client = self._client(entry)
        try:
            await client.flushall()
        except RedisError as error:
            raise MetaServiceError(
                f"pool instance did not answer: {type(error).__name__}"
            ) from error
        finally:
            await client.aclose()


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def _holds_data(client: aioredis.Redis) -> bool:
    info = await client.info("keyspace")
    return any(name.startswith("db") for name in info)


@dataclass
class FakeMetaService:
    """In-memory `MetaService`: `redis://meta-<n>.fake:6379` per name; `down` refuses."""

    instances: dict[str, InstanceSpec] = field(default_factory=dict)
    released: list[tuple[str, bool]] = field(default_factory=list)
    down: bool = False

    async def create_instance(self, name: str) -> InstanceSpec:
        if self.down:
            raise MetaServiceError("meta service unreachable")
        if name not in self.instances:
            n = len(self.instances) + 1
            self.instances[name] = InstanceSpec(
                service_ref=f"fake-{n}",
                url=f"redis://meta-{n}.fake:6379",
                admin_url=None,
                admin_username=None,
                admin_password=f"fake-admin-{n}",
            )
        return self.instances[name]

    async def release_instance(self, service_ref: str, *, discard: bool) -> None:
        if self.down:
            raise MetaServiceError("meta service unreachable")
        self.released.append((service_ref, discard))


def device_meta_url(url: str) -> str:
    """The JuiceFS address of a disk for devices: its instance and database, no credentials."""
    return f"{url.rstrip('/')}/{META_DB}"
