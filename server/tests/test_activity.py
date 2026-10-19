"""
The disks' activity: devices report what their Macs did and are writing, everyone who sees a
disk reads it (read-only included), nobody else does, and events go after 30 days.
"""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.activity import service
from floppy_server.context import Context, utcnow
from floppy_server.models import FileEvent
from tests.conftest import ClientFactory, Org
from tests.helpers import EnrolledDevice, create_disk, enrol_device, invite_and_join, set_access


def event(local_id: int, disk: str, op: str = "added", **extra: Any) -> dict[str, Any]:
    """An event as the app sends it, its time in Unix seconds like the VM's."""
    body = {
        "localId": local_id,
        "at": utcnow().timestamp() - 60 + local_id,
        "diskId": disk,
        "op": op,
        "kind": "file",
        "path": f"Cantine/Girato/clip-{local_id}.mov",
        "size": 1000 * local_id,
    }
    return {**body, **extra}


async def report(client: httpx.AsyncClient, device: EnrolledDevice, **body: Any) -> dict[str, Any]:
    response = await client.post("/device/activity", json=body, headers=device.auth)
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


def past_the_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hands out events at once, as if they had been reported seconds ago."""
    monkeypatch.setattr(service, "HORIZON", timedelta(0))


async def two_people(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> tuple[dict[str, Any], EnrolledDevice, EnrolledDevice, httpx.AsyncClient]:
    """A disk, Giulia's device (admin, write) and Marco's (member, read)."""
    disk = await create_disk(admin, ctx, "Progetti")
    member = clients()
    marco = await invite_and_join(admin, member, ctx, "marco@studio.test")
    await set_access(admin, disk["id"], marco["id"], "read")
    app = clients()
    giulia_mac = await enrol_device(app, admin, ctx, org.admin_email)
    marco_mac = await enrol_device(app, member, ctx, "marco@studio.test", name="Mac di Marco")
    return disk, giulia_mac, marco_mac, app


async def test_events_reach_everyone_who_sees_the_disk_once(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk, giulia, marco, app = await two_people(org, ctx, admin, clients)
    first = await report(app, marco)
    assert (first["events"], first["more"]) == ([], False)
    cursor = first["cursor"]

    renamed = event(
        3, disk["id"], "renamed", kind="folder", path="Backstage", to="Backstage Tosca", size=None
    )
    batch = [event(1, disk["id"]), event(2, disk["id"], "deleted"), renamed]
    await report(app, giulia, events=batch, after=None)
    await report(app, giulia, events=batch, after=None)  # sent again after a lost answer
    # Inside the horizon: not handed out yet, a transaction could still commit behind it.
    assert (await report(app, marco, after=cursor))["events"] == []
    past_the_horizon(monkeypatch)

    seen = await report(app, marco, after=cursor)
    assert [(e["localId"], e["op"]) for e in seen["events"]] == [
        (1, "added"),
        (2, "deleted"),
        (3, "renamed"),
    ]
    assert seen["events"][2]["to"] == "Backstage Tosca"
    assert seen["events"][0]["by"] == {"name": "Giulia", "you": False}
    assert seen["events"][0]["thisDevice"] is False
    assert (await report(app, marco, after=seen["cursor"]))["events"] == []

    own = await report(app, giulia, after=cursor)
    assert own["events"][0]["by"]["you"] is True
    assert own["events"][0]["thisDevice"] is True


async def test_a_disk_the_device_does_not_see_is_neither_stored_nor_shown(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk, giulia, marco, app = await two_people(org, ctx, admin, clients)
    hidden = await create_disk(admin, ctx, "Contabilita")
    cursor = (await report(app, marco))["cursor"]
    # Marco's Mac cannot have done anything on a disk he does not see: dropped.
    await report(app, marco, events=[event(1, hidden["id"])])
    async with owner_engine.connect() as conn:
        stored = (await conn.execute(select(FileEvent.local_id))).scalars().all()
    assert stored == []
    # Giulia's work on it stays hers: Marco sees only Progetti.
    await report(app, giulia, events=[event(1, disk["id"]), event(2, hidden["id"])])
    past_the_horizon(monkeypatch)
    assert [e["localId"] for e in (await report(app, marco, after=cursor))["events"]] == [1]
    # The history of a disk Marco does not see is not his to read.
    response = await app.get(
        "/device/activity", params={"diskId": hidden["id"]}, headers=marco.auth
    )
    assert response.status_code == 404


async def test_what_others_are_writing(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk, giulia, marco, app = await two_people(org, ctx, admin, clients)
    since = utcnow().timestamp() - 30
    clip = {"diskId": disk["id"], "path": "Cantine/Girato/A014.mov", "op": "added"}
    await report(app, giulia, writing=[{**clip, "since": since, "size": 512}])
    seen = (await report(app, marco))["writing"]
    assert len(seen) == 1
    assert seen[0]["path"] == "Cantine/Girato/A014.mov"
    assert (seen[0]["size"], seen[0]["by"]) == (512, {"name": "Giulia", "you": False})
    # Her own writes come from her own Mac, not back from the server.
    assert (await report(app, giulia))["writing"] == []
    # The next report replaces the list: the copy is done.
    await report(app, giulia, writing=[])
    assert (await report(app, marco))["writing"] == []


async def test_history_newest_first_by_pages(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk, giulia, marco, app = await two_people(org, ctx, admin, clients)
    await report(app, giulia, events=[event(n, disk["id"]) for n in range(1, 6)])
    params: dict[str, Any] = {"diskId": disk["id"], "limit": 2}
    pages = []
    while True:
        response = await app.get("/device/activity", params=params, headers=marco.auth)
        assert response.status_code == 200, response.text
        page = response.json()
        pages.append([e["localId"] for e in page["events"]])
        if page["before"] is None:
            break
        params["before"] = page["before"]
    assert pages == [[5, 4], [3, 2], [1]]


async def test_who_deleted_each_trash_entry(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk, giulia, marco, app = await two_people(org, ctx, admin, clients)
    gone = event(1, disk["id"], "deleted", path="Cantine/Girato/clip.mov")
    saved = event(2, disk["id"], "replaced", path="Documenti/musica.xlsx")
    await report(app, giulia, events=[gone, saved, event(3, disk["id"])])
    items = [
        {"path": "Cantine/Girato/clip.mov", "at": gone["at"] + 1.5},
        {"path": "Documenti/musica.xlsx", "at": saved["at"]},
        # The same path deleted ten minutes later: no event says by whom.
        {"path": "Cantine/Girato/clip.mov", "at": gone["at"] + 600},
        {"path": "Cantine/Girato/clip-3.mov", "at": gone["at"]},
    ]
    body = {"diskId": disk["id"], "items": items}
    response = await app.post("/device/activity/deletes", json=body, headers=marco.auth)
    assert response.json()["items"] == [
        {"name": "Giulia", "you": False},
        {"name": "Giulia", "you": False},
        None,
        None,
    ]


async def test_reports_that_leave_the_disk_are_refused(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    disk, giulia, _, app = await two_people(org, ctx, admin, clients)
    for bad in [
        event(1, disk["id"], path="../etc/passwd"),
        event(1, disk["id"], path="/Cantine"),
        event(1, disk["id"], path="a//b"),
        event(1, disk["id"], "renamed"),
        event(1, disk["id"], to="x"),
        event(0, disk["id"]),
    ]:
        response = await app.post("/device/activity", json={"events": [bad]}, headers=giulia.auth)
        assert response.status_code == 422, bad
    response = await app.post("/device/activity", json={"after": "nope"}, headers=giulia.auth)
    assert response.status_code == 422
    response = await app.post("/device/activity", json={})
    assert response.status_code == 401


async def test_events_go_after_thirty_days(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    disk, giulia, _, app = await two_people(org, ctx, admin, clients)
    await report(app, giulia, events=[event(1, disk["id"]), event(2, disk["id"])])
    async with owner_engine.begin() as conn:
        await conn.execute(
            update(FileEvent)
            .where(FileEvent.local_id == 1)
            .values(received_at=utcnow() - timedelta(days=31))
        )
    assert await service.purge(ctx) == 1
    async with owner_engine.connect() as conn:
        left = (await conn.execute(select(FileEvent.local_id))).scalars().all()
    assert left == [2]
