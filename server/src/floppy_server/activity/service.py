"""
The disks' activity (DECISIONS #27): each device reports what its Mac did on the disks it
sees, read from its VM's Samba audit, and what it is writing now; the answer is what anyone
did on those disks since the device's cursor and what the other devices are writing. Events
are visible to everyone who sees the disk, read-only included, and kept 30 days.

Two cursors: the report's follows `received_at`, so an event reported late by a Mac that
was asleep still reaches everyone; the history's follows `occurred_at`, newest first. The
report's stops `HORIZON` short of now: `received_at` is the time a transaction began, and one
still open could otherwise commit behind a cursor that already passed it.
"""

import base64
import binascii
from datetime import datetime, timedelta
from typing import Any, Literal, Self, cast
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy import CursorResult, delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.access.service import wanted
from floppy_server.api import CamelModel
from floppy_server.context import Context, utcnow
from floppy_server.device.service import active_device
from floppy_server.errors import not_found, validation
from floppy_server.models import Device, DeviceActivity, FileEvent, User

KEEP = timedelta(days=30)
HORIZON = timedelta(seconds=5)
# A device reports every few seconds; past this its writes are stale (asleep, gone).
WRITING_TTL = timedelta(seconds=30)
PAGE = 500
# A trash entry's time is the delete's in the VM that made it; the event's comes from the
# same VM's audit, both within a few seconds of each other.
DELETE_WINDOW = timedelta(seconds=120)
LAST_ID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

Op = Literal["created", "added", "written", "replaced", "renamed", "deleted"]
WritingOp = Literal["added", "written", "replaced"]
Kind = Literal["file", "folder"]
DiskPath = Field(min_length=1, max_length=4096)


def _disk_path(value: str | None) -> str | None:
    """A path inside a disk as the VM names it: relative, no empty or dot parts."""
    if value is None:
        return None
    parts = value.split("/")
    if "\0" in value or any(p in ("", ".", "..") for p in parts):
        raise ValueError("not a path inside the disk")
    return value


class EventIn(CamelModel):
    """One thing the device's Mac did on a disk, as its VM recorded it."""

    local_id: int = Field(gt=0, lt=2**63)
    at: datetime
    disk_id: UUID
    op: Op
    kind: Kind
    path: str = DiskPath
    to: str | None = Field(default=None, min_length=1, max_length=4096)
    size: int | None = Field(default=None, ge=0, lt=2**63)

    _paths = field_validator("path", "to")(_disk_path)

    @model_validator(mode="after")
    def _rename_has_a_destination(self) -> Self:
        if (self.op == "renamed") != (self.to is not None):
            raise ValueError("a rename, and only a rename, has `to`")
        return self


class WritingIn(CamelModel):
    """A file the device's Mac is writing now: `added` for a new one arriving."""

    disk_id: UUID
    path: str = DiskPath
    op: WritingOp
    since: datetime
    size: int | None = Field(default=None, ge=0, lt=2**63)

    _paths = field_validator("path")(_disk_path)


class ActivityReport(CamelModel):
    """
    The device's report: its new events, oldest first, what it is writing now (the whole
    list, replacing the last one), and its cursor, null on its first report.
    """

    events: list[EventIn] = Field(default_factory=list, max_length=PAGE)
    writing: list[WritingIn] = Field(default_factory=list, max_length=200)
    after: str | None = Field(default=None, max_length=200)


class Person(CamelModel):
    """Who did it: the person's name, and whether it is the device's own person."""

    name: str
    you: bool


class EventOut(CamelModel):
    """An event on a disk. `thisDevice` with `localId` match the device's own records."""

    id: UUID
    disk_id: UUID
    at: datetime
    op: Op
    kind: Kind
    path: str
    to: str | None
    size: int | None
    by: Person
    this_device: bool
    local_id: int


class WritingOut(CamelModel):
    """A file another device is writing on a disk this device sees."""

    disk_id: UUID
    path: str
    op: WritingOp
    since: datetime
    size: int | None
    by: Person
    reported_at: datetime


class ActivityAnswer(CamelModel):
    """
    What changed on the device's disks since its cursor, oldest first, with the cursor to
    send next; `more` when a page was full. On a first report, no events: the cursor starts
    now. `writing` is what the other devices are writing now.
    """

    events: list[EventOut]
    cursor: str
    more: bool
    writing: list[WritingOut]


class ActivityPage(CamelModel):
    """A page of a disk's history, newest first; `before` asks for the next one."""

    events: list[EventOut]
    before: str | None


class DeleteQuery(CamelModel):
    """A trash entry: where it was and when it was deleted."""

    path: str = DiskPath
    at: datetime


class DeletesRequest(CamelModel):
    """Trash entries of one disk, to learn who deleted or replaced each."""

    disk_id: UUID
    items: list[DeleteQuery] = Field(max_length=1000)


class DeletesAnswer(CamelModel):
    """Who deleted or replaced each entry, in the order asked; null when no event says."""

    items: list[Person | None]


def _encode(at: datetime, event_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{event_id}".encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[datetime, UUID]:
    """The time and id a cursor points after; 422 when it was not made here."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, event_id = raw.partition("|")
        when = datetime.fromisoformat(at)
        if when.tzinfo is None:
            raise ValueError("no time zone")
        return when, UUID(event_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise validation("Invalid cursor") from error


async def _visible(db: AsyncSession, device: Device) -> set[UUID]:
    """The ready disks the device's person sees, read or write."""
    return {
        volume_id
        for (_, volume_id), level in (
            await wanted(db, device.organization_id, device_ids=[device.id])
        ).items()
        if level in ("read", "write")
    }


async def _people(db: AsyncSession, user_ids: set[UUID]) -> dict[UUID, str]:
    if not user_ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))
    ).all()
    return {user_id: name or email for user_id, name, email in rows}


async def _out(db: AsyncSession, device: Device, events: list[FileEvent]) -> list[EventOut]:
    names = await _people(db, {e.user_id for e in events})
    return [
        EventOut(
            id=e.id,
            disk_id=e.volume_id,
            at=e.occurred_at,
            op=e.op,
            kind=e.kind,
            path=e.path,
            to=e.dest,
            size=e.size,
            by=Person(name=names.get(e.user_id, ""), you=e.user_id == device.user_id),
            this_device=e.device_id == device.id,
            local_id=e.local_id,
        )
        for e in events
    ]


async def _writing(db: AsyncSession, device: Device, visible: set[UUID]) -> list[WritingOut]:
    """What the organization's other active devices report writing on the visible disks."""
    since = utcnow() - WRITING_TTL
    rows = (
        await db.execute(
            select(DeviceActivity, Device.user_id)
            .join(
                Device,
                (Device.id == DeviceActivity.device_id)
                & (Device.organization_id == DeviceActivity.organization_id),
            )
            .where(
                DeviceActivity.organization_id == device.organization_id,
                DeviceActivity.device_id != device.id,
                DeviceActivity.reported_at >= since,
                Device.state == "active",
            )
        )
    ).all()
    names = await _people(db, {user_id for _, user_id in rows})
    out = []
    for activity, user_id in rows:
        by = Person(name=names.get(user_id, ""), you=user_id == device.user_id)
        for w in activity.writing:
            item = WritingIn.model_validate(w)
            if item.disk_id in visible:
                out.append(WritingOut(**item.model_dump(), by=by, reported_at=activity.reported_at))
    return out


async def report(ctx: Context, token: str, body: ActivityReport) -> ActivityAnswer:
    """
    Stores the device's events on the disks it sees (an event on any other disk is dropped,
    a repeated one ignored) and what it is writing; answers what changed since `after`.
    """
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await active_device(db, token)
        visible = await _visible(db, device)
        rows = [
            {
                "organization_id": device.organization_id,
                "volume_id": e.disk_id,
                "device_id": device.id,
                "user_id": device.user_id,
                "local_id": e.local_id,
                "occurred_at": e.at,
                "op": e.op,
                "kind": e.kind,
                "path": e.path,
                "dest": e.to,
                "size": e.size,
            }
            for e in body.events
            if e.disk_id in visible
        ]
        if rows:
            await db.execute(
                insert(FileEvent)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["device_id", "local_id"])
            )
        writing = [w.model_dump(mode="json") for w in body.writing if w.disk_id in visible]
        await db.execute(
            insert(DeviceActivity)
            .values(
                device_id=device.id,
                organization_id=device.organization_id,
                writing=writing,
                reported_at=now,
            )
            .on_conflict_do_update(
                index_elements=[DeviceActivity.device_id],
                set_={"writing": writing, "reported_at": now},
            )
        )
        horizon = now - HORIZON
        if body.after is None:
            return ActivityAnswer(
                events=[],
                cursor=_encode(horizon, LAST_ID),
                more=False,
                writing=await _writing(db, device, visible),
            )
        at, event_id = _decode(body.after)
        # A Mac back after more than the retention starts at what is still kept.
        at = max(at, now - KEEP)
        events = list(
            (
                await db.execute(
                    select(FileEvent)
                    .where(
                        FileEvent.organization_id == device.organization_id,
                        FileEvent.volume_id.in_(visible),
                        tuple_(FileEvent.received_at, FileEvent.id) > tuple_(at, event_id),
                        FileEvent.received_at < horizon,
                    )
                    .order_by(FileEvent.received_at, FileEvent.id)
                    .limit(PAGE)
                )
            ).scalars()
        )
        cursor = _encode(events[-1].received_at, events[-1].id) if events else body.after
        return ActivityAnswer(
            events=await _out(db, device, events),
            cursor=cursor,
            more=len(events) == PAGE,
            writing=await _writing(db, device, visible),
        )


async def history(
    ctx: Context, token: str, disk_id: UUID, before: str | None, limit: int
) -> ActivityPage:
    """A page of one disk's events, newest first; 404 for a disk the device does not see."""
    async with ctx.sessions() as db:
        device = await active_device(db, token)
        if disk_id not in await _visible(db, device):
            raise not_found("Not a disk this device sees")
        query = select(FileEvent).where(
            FileEvent.organization_id == device.organization_id,
            FileEvent.volume_id == disk_id,
            FileEvent.occurred_at >= utcnow() - KEEP,
        )
        if before:
            at, event_id = _decode(before)
            query = query.where(tuple_(FileEvent.occurred_at, FileEvent.id) < tuple_(at, event_id))
        events = list(
            (
                await db.execute(
                    query.order_by(FileEvent.occurred_at.desc(), FileEvent.id.desc()).limit(
                        limit + 1
                    )
                )
            ).scalars()
        )
        page = events[:limit]
        return ActivityPage(
            events=await _out(db, device, page),
            before=_encode(page[-1].occurred_at, page[-1].id) if len(events) > limit else None,
        )


async def deletes(ctx: Context, token: str, body: DeletesRequest) -> DeletesAnswer:
    """For each trash entry, who deleted or replaced that path closest to its time."""
    async with ctx.sessions() as db:
        device = await active_device(db, token)
        if body.disk_id not in await _visible(db, device) or not body.items:
            return DeletesAnswer(items=[None] * len(body.items))
        paths = {i.path for i in body.items}
        lo = min(i.at for i in body.items) - DELETE_WINDOW
        hi = max(i.at for i in body.items) + DELETE_WINDOW
        rows = (
            await db.execute(
                select(FileEvent.path, FileEvent.occurred_at, FileEvent.user_id).where(
                    FileEvent.organization_id == device.organization_id,
                    FileEvent.volume_id == body.disk_id,
                    FileEvent.path.in_(paths),
                    FileEvent.op.in_(("deleted", "replaced")),
                    FileEvent.occurred_at.between(lo, hi),
                )
            )
        ).all()
        by_path: dict[str, list[tuple[datetime, UUID]]] = {}
        for path, at, user_id in rows:
            by_path.setdefault(path, []).append((at, user_id))
        names = await _people(db, {user_id for _, _, user_id in rows})
        out: list[Person | None] = []
        for item in body.items:
            near = [
                (abs(at - item.at), user_id)
                for at, user_id in by_path.get(item.path, [])
                if abs(at - item.at) <= DELETE_WINDOW
            ]
            if not near:
                out.append(None)
                continue
            user_id = min(near)[1]
            out.append(Person(name=names.get(user_id, ""), you=user_id == device.user_id))
        return DeletesAnswer(items=out)


async def purge(ctx: Context) -> int:
    """Deletes the events older than the retention; returns how many."""
    async with ctx.sessions() as db, db.begin():
        result = cast(
            "CursorResult[Any]",
            await db.execute(delete(FileEvent).where(FileEvent.received_at < utcnow() - KEEP)),
        )
        return result.rowcount
