"""
Disks (volumes): the list, creation with `juicefs format`, and the two settings that change
later (trash days through `juicefs config`, the cache expiry at the next mount).

Creating a disk (docs/DATABASE_SCHEMA.md, "volumes"): under the organization's lock the
lowest Valkey database never used is allocated, JuiceFS's name is derived from the display
name, and the row is inserted as `creating`; the answer goes back at once. The format is a
job: `run_disk_formats` (the worker) takes `creating` rows with `SKIP LOCKED` and makes them
`ready`, or `failed` and soft deleted with their database number and prefix burned, since a
half-written format is never reused. A disk is never created while a rotation applies, so
it cannot end up on a key the rotation already left behind.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import any_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, DetailValue, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, invalid_state, not_found
from floppy_server.infra.juicefs import FormatError
from floppy_server.keys.service import active_key, rotation_in_progress
from floppy_server.models import (
    Device,
    DeviceStatus,
    KeyRotation,
    Organization,
    User,
    Volume,
    VolumeStatus,
    lock_organization,
)

log = logging.getLogger(__name__)

CONNECTED_WITHIN = timedelta(minutes=2)
BLOCK_SIZE_KB = 1024  # DECISIONS #15
MOUNT_ROOT = "/Volumes/Floppy"
# The folder in /Volumes/Floppy (DATABASE_SCHEMA.md, "Disks on a Mac").
DISK_NAME_PATTERN = r"^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,58}[A-Za-z0-9])?$"
VOLUME_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")


class CachePolicy(CamelModel):
    """The cache option every Mac mounting the disk gets: only the expiry."""

    expire_days: int = Field(ge=1, le=365)


class DiskError(CamelModel):
    """Why a creation failed; never a secret."""

    code: str
    message: str


class DiskOut(CamelModel):
    """A disk, as the admin web shows it. JuiceFS's name is not part of it."""

    id: UUID
    name: str
    mount_path: str
    state: Literal["creating", "ready", "failed"]
    error: DiskError | None
    status: Literal["online", "unreachable"]
    bucket: str
    region: str
    endpoint: str
    meta_db: int
    block_size_kb: int
    trash_days: int
    trash_bytes: int
    used_bytes: int
    file_count: int
    last_write_at: datetime | None
    mounted_by: int
    cache: CachePolicy
    created_at: datetime
    created_by: str


class CreateDiskRequest(CamelModel):
    """A new disk: its name, which is its folder on every Mac, and the trash retention."""

    name: str = Field(pattern=DISK_NAME_PATTERN)
    trash_days: int = Field(default=7, ge=0, le=365)


class UpdateDiskRequest(CamelModel):
    """Trash days (through `juicefs config`) and the cache policy (at the next mount)."""

    trash_days: int | None = Field(default=None, ge=0, le=365)
    cache: CachePolicy | None = None


def disk_name_taken() -> ApiError:
    """409: a live disk has the same name, compared case-insensitively as macOS does."""
    return ApiError(409, "disk_name_taken", "Name already in use")


def disk_limit_reached(limit: int) -> ApiError:
    """409: every Valkey database of the instance is taken (database 0 stays empty)."""
    return ApiError(
        409, "disk_limit_reached", "No Valkey database left for a new disk", {"limit": limit}
    )


def format_failed(message: str) -> ApiError:
    """502: `juicefs config` failed; nothing changed."""
    return ApiError(502, "format_failed", "JuiceFS refused the change", {"reason": message[:300]})


def mount_path(name: str) -> str:
    """Where a disk appears on every Mac: a folder of the one share."""
    return f"{MOUNT_ROOT}/{name}"


def derive_volume_name(name: str, taken: set[str]) -> str:
    """
    JuiceFS's name for a new disk, derived once from its display name: lowercased, every run
    of other characters a hyphen, then `-2`, `-3`... while taken (the admin web's fake does
    the same). Names of failed and deleted disks stay taken: their objects are still there.
    """
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:58].rstrip("-")
    if VOLUME_NAME.fullmatch(base) and base not in taken:
        return base
    n = 2
    while True:
        candidate = f"{base or 'disk'}-{n}"
        if VOLUME_NAME.fullmatch(candidate) and candidate not in taken:
            return candidate
        n += 1


async def _disks(
    db: AsyncSession, organization_id: UUID, volume_id: UUID | None = None
) -> list[DiskOut]:
    now = utcnow()
    mounted = (
        select(func.count())
        .select_from(DeviceStatus)
        .join(
            Device,
            (Device.id == DeviceStatus.device_id)
            & (Device.organization_id == DeviceStatus.organization_id),
        )
        .where(
            DeviceStatus.organization_id == organization_id,
            Device.state == "active",
            DeviceStatus.last_seen_at > now - CONNECTED_WITHIN,
            Volume.id == any_(DeviceStatus.mounted_volume_ids),
        )
        .scalar_subquery()
    )
    query = (
        select(Volume, VolumeStatus, User, mounted)
        .outerjoin(
            VolumeStatus,
            (VolumeStatus.volume_id == Volume.id)
            & (VolumeStatus.organization_id == Volume.organization_id),
        )
        .join(User, User.id == Volume.created_by_user_id)
        .where(Volume.organization_id == organization_id)
        .order_by(Volume.created_at)
    )
    # The list shows live disks; one disk is found by id even after its creation failed.
    query = (
        query.where(Volume.id == volume_id)
        if volume_id
        else query.where(Volume.deleted_at.is_(None))
    )
    rows = (await db.execute(query)).all()
    return [
        DiskOut(
            id=v.id,
            name=v.name,
            mount_path=mount_path(v.name),
            state=v.state,
            error=DiskError(code="format_failed", message=v.format_error)
            if v.format_error
            else None,
            status="online" if status is None or status.reachable else "unreachable",
            bucket=v.s3_bucket,
            region=v.s3_region,
            endpoint=v.s3_endpoint,
            meta_db=v.meta_db,
            block_size_kb=v.block_size_kb,
            trash_days=v.trash_days,
            trash_bytes=status.trash_bytes if status else 0,
            used_bytes=status.used_bytes if status else 0,
            file_count=status.file_count if status else 0,
            last_write_at=status.last_write_at if status else None,
            mounted_by=count or 0,
            cache=CachePolicy(expire_days=v.cache_expire_days),
            created_at=v.created_at,
            created_by=creator.name or creator.email,
        )
        for v, status, creator, count in rows
    ]


async def list_disks(ctx: Context, actor: Actor) -> list[DiskOut]:
    """The organization's live disks, `creating` ones included."""
    async with ctx.sessions() as db:
        return await _disks(db, actor.organization_id)


async def get_disk(ctx: Context, organization_id: UUID, volume_id: UUID) -> DiskOut:
    """One disk, also after its creation failed."""
    async with ctx.sessions() as db:
        disks = await _disks(db, organization_id, volume_id)
    if not disks:
        raise not_found("No such disk")
    return disks[0]


async def _free_meta_db(db: AsyncSession, org: Organization) -> int | None:
    """The lowest database number never used by any disk, deleted ones included."""
    used = set(
        (await db.execute(select(Volume.meta_db).where(Volume.organization_id == org.id))).scalars()
    )
    return next((n for n in range(1, org.valkey_databases) if n not in used), None)


async def create_disk(ctx: Context, actor: Actor, body: CreateDiskRequest) -> DiskOut:
    """Records a new disk as `creating`; the worker formats it."""
    name = body.name
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, actor.organization_id)
        org = await db.get(Organization, actor.organization_id)
        if org is None:
            raise not_found()
        applying = await db.scalar(
            select(func.count()).where(
                KeyRotation.organization_id == org.id, KeyRotation.state == "applying"
            )
        )
        if applying:
            raise rotation_in_progress()
        if await db.scalar(
            select(func.count()).where(
                Volume.organization_id == org.id,
                func.lower(Volume.name) == name.lower(),
                Volume.deleted_at.is_(None),
            )
        ):
            raise disk_name_taken()
        if await active_key(db, org.id) is None:
            raise invalid_state("The organization has no active S3 key")
        meta_db = await _free_meta_db(db, org)
        if meta_db is None:
            raise disk_limit_reached(org.valkey_databases - 1)
        taken = set(
            (
                await db.execute(select(Volume.volume_name).where(Volume.organization_id == org.id))
            ).scalars()
        )
        volume = Volume(
            organization_id=org.id,
            name=name,
            volume_name=derive_volume_name(name, taken),
            s3_endpoint=org.s3_endpoint,
            s3_region=org.s3_region,
            s3_bucket=org.s3_bucket,
            meta_db=meta_db,
            block_size_kb=BLOCK_SIZE_KB,
            trash_days=body.trash_days,
            created_by_user_id=actor.user_id,
        )
        db.add(volume)
        await db.flush()
        db.add(VolumeStatus(volume_id=volume.id, organization_id=org.id))
        record(
            db,
            org.id,
            "disk_created",
            AuditActor.person(actor.user_id, actor.label),
            Target("disk", volume.id, name),
            {"blockSize": "1 MB"},
        )
        volume_id = volume.id
    ctx.disk_wakeup.set()
    return await get_disk(ctx, actor.organization_id, volume_id)


async def run_disk_formats(ctx: Context) -> int:
    """
    Formats every `creating` disk not taken by another process, one per transaction; the row
    stays locked while JuiceFS runs. Returns how many were handled.
    """
    handled = 0
    while True:
        async with ctx.sessions() as db, db.begin():
            volume = (
                await db.execute(
                    select(Volume)
                    .where(Volume.state == "creating", Volume.deleted_at.is_(None))
                    .order_by(Volume.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if volume is None:
                return handled
            await _format(ctx, db, volume)
            handled += 1


async def _format(ctx: Context, db: AsyncSession, volume: Volume) -> None:
    org = await db.get(Organization, volume.organization_id)
    key = await active_key(db, volume.organization_id)
    if org is None or key is None:
        _fail(volume, "The organization has no active S3 key")
        return
    try:
        juicefs_uuid = await ctx.formatter.format(
            ctx.volume_location(org, volume),
            ctx.credentials(key),
            block_size_kb=volume.block_size_kb,
            trash_days=volume.trash_days,
        )
    except FormatError as error:
        log.warning("format of a new disk failed: %s", error)
        _fail(volume, str(error))
        return
    volume.state = "ready"
    volume.juicefs_uuid = juicefs_uuid
    volume.s3_key_id = key.id


def _fail(volume: Volume, message: str) -> None:
    volume.state = "failed"
    volume.format_error = message[:500]
    volume.deleted_at = utcnow()


async def update_disk(
    ctx: Context, actor: Actor, volume_id: UUID, body: UpdateDiskRequest
) -> DiskOut:
    """Changes trash days (format first, then the row) and the cache expiry."""
    async with ctx.sessions() as db, db.begin():
        volume = (
            await db.execute(
                select(Volume)
                .where(
                    Volume.organization_id == actor.organization_id,
                    Volume.id == volume_id,
                    Volume.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        org = await db.get(Organization, actor.organization_id)
        if volume is None or org is None:
            raise not_found("No such disk")
        if volume.state != "ready":
            raise invalid_state("The disk is not ready")
        changes: dict[str, DetailValue] = {}
        if body.trash_days is not None and body.trash_days != volume.trash_days:
            try:
                await ctx.formatter.set_trash_days(
                    ctx.volume_location(org, volume), body.trash_days
                )
            except FormatError as error:
                raise format_failed(str(error)) from error
            changes["trashDays"] = [volume.trash_days, body.trash_days]
            volume.trash_days = body.trash_days
        if body.cache is not None and body.cache.expire_days != volume.cache_expire_days:
            changes["cacheExpireDays"] = [volume.cache_expire_days, body.cache.expire_days]
            volume.cache_expire_days = body.cache.expire_days
        if changes:
            record(
                db,
                actor.organization_id,
                "disk_updated",
                AuditActor.person(actor.user_id, actor.label),
                Target("disk", volume.id, volume.name),
                changes,
            )
    return await get_disk(ctx, actor.organization_id, volume_id)
