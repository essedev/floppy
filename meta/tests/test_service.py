"""
The service end to end over its API, with real valkey-server processes: creation, the
settings each instance runs with, restarts after a crash and after the service itself
restarts, stop and start, discard, reconfiguration, authentication and the routes.
"""

import asyncio
import os
import signal

import pytest
import redis.asyncio as aioredis
from redis.exceptions import AuthenticationError, ResponseError

from tests.conftest import ROUTES_TOKEN, TOKEN, Service

DEVICE = "floppy_d_" + "a" * 32


def admin(port: int, password: str) -> aioredis.Redis:
    return aioredis.Redis(host="127.0.0.1", port=port, password=password, socket_timeout=5)


async def config_of(client: aioredis.Redis, name: str) -> str:
    value = (await client.config_get(name))[name]
    return str(value)


async def test_an_instance_is_created_once_and_runs_with_settings_for_metadata(
    service: Service,
) -> None:
    created = await service.api.put("/v1/instances/disk-a")
    assert created.status_code == 201
    spec = created.json()
    assert spec["url"] == f"rediss://{spec['label']}.meta.floppy.test:443"
    assert spec["admin_username"] is None
    again = await service.api.put("/v1/instances/disk-a")
    assert again.status_code == 200
    assert again.json() == spec  # idempotent by name: same label, port and password
    client = admin(spec["port"], spec["admin_password"])
    try:
        assert await client.ping()
        assert await config_of(client, "appendonly") == "yes"
        assert await config_of(client, "appendfsync") == "always"
        assert await config_of(client, "repl-diskless-sync-delay") == "0"
        assert await config_of(client, "maxmemory-policy") == "noeviction"
        assert await config_of(client, "databases") == "2"
    finally:
        await client.aclose()
    wrong = admin(spec["port"], "not-the-password")
    with pytest.raises(AuthenticationError):
        await wrong.ping()
    await wrong.aclose()
    other = (await service.api.put("/v1/instances/disk-b")).json()
    assert other["port"] != spec["port"] and other["label"] != spec["label"]
    status = (await service.api.get("/v1/instances/disk-a")).json()
    assert status["state"] == "running" and status["desired"] == "running"
    assert "admin_password" not in status
    assert status["details"]["aof_last_write_status"] == "ok"


async def test_a_crashed_instance_restarts_with_its_data(service: Service) -> None:
    spec = (await service.api.put("/v1/instances/disk-a")).json()
    client = admin(spec["port"], spec["admin_password"])
    await client.execute_command("SELECT", 1)
    await client.set("setting", "tree")
    pid = (await service.api.get("/v1/instances/disk-a")).json()["pid"]
    os.kill(pid, signal.SIGKILL)
    for _ in range(100):
        status = (await service.api.get("/v1/instances/disk-a")).json()
        if status["state"] == "running" and status["pid"] != pid:
            break
        await asyncio.sleep(0.1)
    assert status["restarts"] == 1
    assert status["last_exit_code"] == -signal.SIGKILL
    fresh = aioredis.Redis(
        host="127.0.0.1", port=spec["port"], password=spec["admin_password"], db=1
    )
    try:
        # appendfsync always: a write acknowledged before the crash is on disk.
        assert await fresh.get("setting") == b"tree"
    finally:
        await fresh.aclose()
        await client.aclose()


async def test_instances_come_back_after_the_service_restarts_with_their_users(
    service: Service,
) -> None:
    spec = (await service.api.put("/v1/instances/disk-a")).json()
    stopped = (await service.api.put("/v1/instances/disk-b")).json()
    await service.api.post("/v1/instances/disk-b/stop", json={"discard": False})
    client = aioredis.Redis(
        host="127.0.0.1", port=spec["port"], password=spec["admin_password"], db=1
    )
    await client.set("setting", "tree")
    # A device's user, as the control plane creates it; the service saves it to the ACL file.
    await client.execute_command(
        "ACL", "SETUSER", DEVICE, "reset", "on", ">device-pw", "~*", "+@read", "+select"
    )
    await client.aclose()
    await service.supervisor.shutdown()

    async with Service(service.settings) as restarted:
        status = (await restarted.api.get("/v1/instances/disk-a")).json()
        assert status["desired"] == "running"
        runner = restarted.supervisor.runners["disk-a"]
        await runner.wait_ready(10)
        device = aioredis.Redis(
            host="127.0.0.1", port=spec["port"], username=DEVICE, password="device-pw", db=1
        )
        try:
            assert await device.get("setting") == b"tree"
        finally:
            await device.aclose()
        # The stopped one stays stopped, its data kept.
        assert (await restarted.api.get("/v1/instances/disk-b")).json()["state"] == "stopped"
        assert restarted.supervisor.runners["disk-b"].instance.port == stopped["port"]


async def test_stop_start_and_discard(service: Service) -> None:
    spec = (await service.api.put("/v1/instances/disk-a")).json()
    client = aioredis.Redis(
        host="127.0.0.1", port=spec["port"], password=spec["admin_password"], db=1
    )
    await client.set("setting", "tree")
    await client.aclose()
    assert (await service.api.post("/v1/instances/disk-a/stop")).json() == {"state": "stopped"}
    routes = await service.api.get(
        "/v1/traefik", headers={"authorization": f"Bearer {ROUTES_TOKEN}"}
    )
    assert routes.json() == {}
    # Asking for it again starts it with its data: "the one it already has".
    assert (await service.api.put("/v1/instances/disk-a")).json() == spec
    client = aioredis.Redis(
        host="127.0.0.1", port=spec["port"], password=spec["admin_password"], db=1
    )
    assert await client.get("setting") == b"tree"
    await client.aclose()
    discarded = await service.api.post("/v1/instances/disk-a/stop", json={"discard": True})
    assert discarded.json() == {"state": "discarded"}
    assert (await service.api.get("/v1/instances/disk-a")).status_code == 404
    trash = list((service.settings.data_dir / "trash").iterdir())
    assert len(trash) == 1 and trash[0].name.startswith("disk-a.")
    fresh = (await service.api.put("/v1/instances/disk-a")).json()
    assert fresh["label"] != spec["label"]
    client = aioredis.Redis(
        host="127.0.0.1", port=fresh["port"], password=fresh["admin_password"], db=1
    )
    assert await client.get("setting") is None
    await client.aclose()
    assert (await service.api.post("/v1/instances/nothing/stop")).status_code == 404


async def test_reconfigure_changes_memory_and_rotates_the_admin_password(
    service: Service,
) -> None:
    spec = (await service.api.put("/v1/instances/disk-a", json={"maxmemory_mb": 64})).json()
    assert spec["maxmemory_mb"] == 64
    changed = await service.api.patch(
        "/v1/instances/disk-a", json={"maxmemory_mb": 128, "rotate_admin_password": True}
    )
    assert changed.status_code == 200
    new = changed.json()
    assert new["admin_password"] != spec["admin_password"] and new["maxmemory_mb"] == 128
    old = admin(spec["port"], spec["admin_password"])
    with pytest.raises((AuthenticationError, ResponseError)):
        await old.ping()
    await old.aclose()
    client = admin(new["port"], new["admin_password"])
    assert await config_of(client, "maxmemory") == str(128 * 1024 * 1024)
    await client.aclose()
    # Only the memory: the password stays.
    kept = (await service.api.patch("/v1/instances/disk-a", json={"maxmemory_mb": None})).json()
    assert kept["admin_password"] == new["admin_password"] and kept["maxmemory_mb"] is None
    # The rotated password survives a restart (it is in the ACL file and the record).
    await service.supervisor.shutdown()
    async with Service(service.settings) as restarted:
        await restarted.supervisor.runners["disk-a"].wait_ready(10)
        client = admin(new["port"], new["admin_password"])
        assert await client.ping()
        assert await config_of(client, "maxmemory") == "0"
        await client.aclose()


async def test_tokens_guard_the_api_and_the_routes(service: Service) -> None:
    api = service.api
    anonymous = {"authorization": ""}
    assert (await api.put("/v1/instances/disk-a", headers=anonymous)).status_code == 401
    wrong = {"authorization": f"Bearer {TOKEN}x"}
    assert (await api.get("/v1/instances", headers=wrong)).status_code == 401
    # The routes token reads routes, nothing else; the control token does not read routes.
    routes_only = {"authorization": f"Bearer {ROUTES_TOKEN}"}
    assert (await api.get("/v1/instances", headers=routes_only)).status_code == 401
    assert (await api.get("/v1/traefik")).status_code == 401
    assert (await api.put("/v1/instances/..%2Fetc")).status_code in (404, 422)
    assert (await api.put("/v1/instances/bad.name")).status_code == 422
    assert (await api.get("/healthz", headers=anonymous)).json()["ok"] is True
    spec = (await api.put("/v1/instances/disk-a")).json()
    routes = (await api.get("/v1/traefik", headers=routes_only)).json()
    assert list(routes["tcp"]["routers"]) == [f"floppy-meta-{spec['label']}"]
    assert spec["admin_password"] not in str(routes)
