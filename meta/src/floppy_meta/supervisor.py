"""
The valkey-server processes: one per instance, children of this service, restarted when they
exit unexpectedly and stopped gracefully (ACL users saved first) when the service stops.

`Supervisor` owns the store and every runner: the API calls it, never the store directly, so
creation, reconfiguration and stops of one instance never interleave.
"""

import asyncio
import contextlib
import logging
import signal
import time
from dataclasses import dataclass
from typing import Any, Literal

import redis.asyncio as aioredis
from redis.exceptions import BusyLoadingError, RedisError, ResponseError

from floppy_meta.config import Settings
from floppy_meta.store import Instance, Store, StoreError, new_password

log = logging.getLogger("floppy_meta")

State = Literal["starting", "running", "backoff", "stopped"]
BACKOFF_MAX_SECONDS = 30.0
# A process that ran this long before exiting resets the backoff: it was not a crash loop.
STABLE_SECONDS = 60.0


class SupervisorError(Exception):
    """An operation that could not complete; the message names no secret."""


class NotFoundError(SupervisorError):
    """No instance has that name."""


@dataclass(frozen=True)
class Status:
    """What an instance is doing, as the API reports it."""

    state: State
    pid: int | None
    restarts: int
    last_exit_code: int | None
    uptime_seconds: float | None


class Runner:
    """One instance's process: started, watched, restarted with backoff, stopped."""

    def __init__(self, settings: Settings, store: Store, instance: Instance) -> None:
        self.settings = settings
        self.store = store
        self.instance = instance
        self.state: State = "stopped"
        self.restarts = 0
        self.last_exit_code: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._started_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._want = False

    def client(self, timeout: float = 5.0) -> aioredis.Redis:
        """An admin connection to the instance, on the container's loopback."""
        return aioredis.Redis(
            host="127.0.0.1",
            port=self.instance.port,
            password=self.instance.admin_password,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
        )

    def status(self) -> Status:
        """The runner's state, process and restart count."""
        process = self._process
        alive = process is not None and process.returncode is None
        return Status(
            state=self.state,
            pid=process.pid if alive and process else None,
            restarts=self.restarts,
            last_exit_code=self.last_exit_code,
            uptime_seconds=time.monotonic() - self._started_at
            if alive and self._started_at
            else None,
        )

    def start(self) -> None:
        """Starts the process and keeps it running until `stop`."""
        if self._task is not None and not self._task.done():
            return
        self._want = True
        self._task = asyncio.create_task(self._run(), name=f"valkey:{self.instance.name}")

    async def _run(self) -> None:
        delay = 1.0
        while self._want:
            config = self.store.render_config(self.instance)
            self.state = "starting"
            process = await asyncio.create_subprocess_exec(
                self.settings.valkey_server,
                str(config),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # Its own process group: a Ctrl-C meant for the service must not reach it
                # before the ACL users are saved.
                start_new_session=True,
            )
            self._process, self._started_at = process, time.monotonic()
            pump = asyncio.create_task(self._pump(process))
            code = await process.wait()
            await pump
            ran = time.monotonic() - self._started_at
            self._process = None
            if not self._want:
                break
            self.restarts += 1
            self.last_exit_code = code
            delay = 1.0 if ran > STABLE_SECONDS else min(delay * 2, BACKOFF_MAX_SECONDS)
            log.warning(
                "instance %s exited with %s after %.0f s; restarting in %.0f s",
                self.instance.name,
                code,
                ran,
                delay,
            )
            self.state = "backoff"
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)
        self.state = "stopped"

    async def _pump(self, process: asyncio.subprocess.Process) -> None:
        """Valkey's log lines, prefixed with the instance's name, into the service's log."""
        if process.stdout is None:
            return
        async for raw in process.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                log.info("[%s] %s", self.instance.name, line)
            if self.state == "starting" and "Ready to accept connections" in line:
                self.state = "running"

    async def wait_ready(self, limit: float) -> None:
        """Returns once the instance answers PING with its data loaded, within `limit` s."""
        deadline = time.monotonic() + limit
        while True:
            client = self.client(timeout=2.0)
            try:
                if await client.ping():
                    self.state = "running"
                    return
            except BusyLoadingError:
                pass  # still loading its AOF
            except RedisError:
                pass
            finally:
                await client.aclose()
            if time.monotonic() > deadline:
                raise SupervisorError(f"instance did not answer within {limit:.0f} s")
            await asyncio.sleep(0.1)

    async def save_acl(self) -> None:
        """Writes the instance's current ACL users to its ACL file."""
        client = self.client()
        try:
            await client.execute_command("ACL", "SAVE")
        finally:
            await client.aclose()

    async def stop(self) -> None:
        """Saves the ACL users, then stops the process gracefully (SIGTERM, then SIGKILL)."""
        self._want = False
        self._wake.set()
        process = self._process
        if process is not None and process.returncode is None:
            try:
                await self.save_acl()
            except RedisError as error:
                log.warning("instance %s: ACL SAVE failed: %s", self.instance.name, error)
            process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), self.settings.stop_timeout_seconds)
            except TimeoutError:
                log.error("instance %s did not stop in time: killed", self.instance.name)
                process.kill()
                await process.wait()
        if self._task is not None:
            await self._task
            self._task = None
        self.state = "stopped"


class Supervisor:
    """Every instance of the volume: their records and their processes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings)
        self.runners: dict[str, Runner] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._create_lock = asyncio.Lock()
        self._saver: asyncio.Task[None] | None = None

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    async def start(self) -> None:
        """Starts every instance the volume says should run, and the periodic ACL save."""
        for instance in self.store.load_all():
            runner = Runner(self.settings, self.store, instance)
            self.runners[instance.name] = runner
            if instance.desired == "running":
                runner.start()
        log.info(
            "meta service: %d instances, %d to run",
            len(self.runners),
            sum(1 for r in self.runners.values() if r.instance.desired == "running"),
        )
        self._saver = asyncio.create_task(self._save_loop())

    async def shutdown(self) -> None:
        """Stops every instance, all at once, each after saving its ACL users."""
        if self._saver is not None:
            self._saver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._saver
        await asyncio.gather(*(r.stop() for r in self.runners.values()))

    async def _save_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.acl_save_seconds)
            for runner in list(self.runners.values()):
                if runner.state != "running":
                    continue
                try:
                    await runner.save_acl()
                except RedisError as error:
                    log.warning("instance %s: ACL SAVE failed: %s", runner.instance.name, error)

    def _runner(self, name: str) -> Runner:
        runner = self.runners.get(name)
        if runner is None:
            raise NotFoundError("no such instance")
        return runner

    async def ensure(self, name: str, maxmemory_mb: int | None) -> tuple[Instance, bool]:
        """
        The instance called `name`, running: the existing one (started again if it was
        stopped) or a new empty one. Returns it and whether it was created.
        """
        async with self._lock(name):
            runner = self.runners.get(name)
            created = False
            if runner is None:
                async with self._create_lock:
                    try:
                        instance = self.store.create(name, maxmemory_mb)
                    except StoreError as error:
                        raise SupervisorError(str(error)) from error
                runner = Runner(self.settings, self.store, instance)
                self.runners[name] = runner
                created = True
            elif runner.instance.desired != "running":
                runner.instance.desired = "running"
                self.store.save(runner.instance)
            runner.start()
            await runner.wait_ready(self.settings.ready_timeout_seconds)
            return runner.instance, created

    async def stop(self, name: str, *, discard: bool) -> None:
        """Stops an instance; its data stays unless `discard`, which moves it to the trash."""
        async with self._lock(name):
            runner = self._runner(name)
            runner.instance.desired = "stopped"
            self.store.save(runner.instance)
            await runner.stop()
            if discard:
                target = self.store.discard(name)
                del self.runners[name]
                log.info("instance %s discarded to %s", name, target.name)

    async def start_instance(self, name: str) -> Instance:
        """Starts a stopped instance again, with its data."""
        async with self._lock(name):
            runner = self._runner(name)
            runner.instance.desired = "running"
            self.store.save(runner.instance)
            runner.start()
            await runner.wait_ready(self.settings.ready_timeout_seconds)
            return runner.instance

    async def reconfigure(
        self, name: str, *, maxmemory_mb: int | None, change_maxmemory: bool, rotate: bool
    ) -> Instance:
        """
        Changes an instance's memory limit and rotates its admin password, live when it
        runs; a rotation needs it running, since the password's hash lives in its ACL file.
        """
        async with self._lock(name):
            runner = self._runner(name)
            instance = runner.instance
            running = runner.state == "running"
            if rotate and not running:
                raise SupervisorError("the admin password can be rotated only while it runs")
            if change_maxmemory:
                instance.maxmemory_mb = maxmemory_mb
                if running:
                    await self._command(
                        runner, "CONFIG", "SET", "maxmemory", f"{maxmemory_mb or 0}mb"
                    )
            if rotate:
                password = new_password()
                await self._command(
                    runner, "ACL", "SETUSER", "default", "resetpass", f">{password}"
                )
                instance.admin_password = password
                await runner.save_acl()
            self.store.save(instance)
            return instance

    async def _command(self, runner: Runner, *args: str) -> Any:
        client = runner.client()
        try:
            return await client.execute_command(*args)
        except ResponseError as error:
            raise SupervisorError(f"the instance refused {args[0]} {args[1]}") from error
        except RedisError as error:
            raise SupervisorError(f"the instance did not answer: {type(error).__name__}") from error
        finally:
            await client.aclose()

    def get(self, name: str) -> tuple[Instance, Status]:
        """An instance and its status."""
        runner = self._runner(name)
        return runner.instance, runner.status()

    def all(self) -> list[tuple[Instance, Status]]:
        """Every instance and its status, in name order."""
        return [(r.instance, r.status()) for _, r in sorted(self.runners.items())]

    async def details(self, name: str) -> dict[str, Any]:
        """Live figures from a running instance: memory, keys, replicas, persistence."""
        runner = self._runner(name)
        if runner.state != "running":
            return {}
        client = runner.client(timeout=2.0)
        try:
            info = await client.info()
        except RedisError:
            return {}
        finally:
            await client.aclose()
        keys = info.get("db1", {})
        return {
            "used_memory_bytes": info.get("used_memory"),
            "keys": keys.get("keys", 0) if isinstance(keys, dict) else 0,
            "connected_replicas": info.get("connected_slaves"),
            "connected_clients": info.get("connected_clients"),
            "aof_last_write_status": info.get("aof_last_write_status"),
            "rdb_last_bgsave_status": info.get("rdb_last_bgsave_status"),
        }
