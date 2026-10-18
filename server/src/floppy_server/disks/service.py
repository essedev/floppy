"""
Disks (volumes): the list, creation with its own instance, bucket and format, the two settings
that change later (trash days through `juicefs config`, the cache expiry at the next mount),
deletion, and a probe of the disk's bucket.

Creating a disk (docs/DATABASE_SCHEMA.md, "volumes"): under the organization's lock the name
and the bucket are checked, JuiceFS's name is derived from the display name, and the row is
inserted as `creating` with its key (manual, probed first) and the access the request carries;
the answer goes back at once. The rest is a job: `run_disk_formats` takes `creating` rows with
`SKIP LOCKED`, asks the meta service for the disk's instance, creates the bucket (managed),
runs `juicefs format` and makes the disk `ready`, then gives the devices that may see it their
users and grants. A failed step makes it `failed` and soft deleted, its key rejected and its
instance wiped: a half-written format is never reused.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid7

from pydantic import Field, StringConstraints
from sqlalchemy import any_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.access.service import AccessEntry, apply_owed, put_access, sync
from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, DetailValue, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, invalid_state, not_found, validation
from floppy_server.infra.juicefs import FormatError, S3Credentials
from floppy_server.infra.meta import MetaServiceError, device_meta_url
from floppy_server.infra.providers import ProviderError
from floppy_server.infra.storage import ProbeResult
from floppy_server.keys.managed import control_plane_key
from floppy_server.keys.schemas import S3KeyIn
from floppy_server.keys.service import (
    instance_of,
    new_key_row,
    probe,
    probe_failed,
    record_probe,
    storage_key,
)
from floppy_server.models import (
    Device,
    DeviceStatus,
    Membership,
    MetaInstance,
    Organization,
    S3Key,
    User,
    Volume,
    VolumeStatus,
    lock_organization,
)

log = logging.getLogger(__name__)

CONNECTED_WITHIN = timedelta(minutes=2)
BLOCK_SIZE_KB = 1024  # DECISIONS #15
MOUNT_ROOT = "/Volumes/Floppy"
# The folder in /Volumes/Floppy (DATABASE_SCHEMA.md, "Disks on a device").
DISK_NAME_PATTERN = r"^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,58}[A-Za-z0-9])?$"
VOLUME_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
# S3 bucket names: 3-63 characters, lowercase letters, digits, dots and hyphens.
BUCKET_PATTERN = r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class CachePolicy(CamelModel):
    """The cache option every device mounting the disk gets: only the expiry."""

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
    meta_url: str | None
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


class DiskStorageIn(S3KeyIn):
    """Manual mode: the disk's own bucket and its key, made by the admin at the provider."""

    endpoint: Text = ""
    region: Text = ""
    bucket: str = Field(pattern=BUCKET_PATTERN)


class CreateDiskRequest(CamelModel):
    """
    A new disk: its name (its folder on every device), the trash retention, its bucket and key
    in manual mode, and who may see it (the creator gets `write` unless listed).
    """

    name: str = Field(pattern=DISK_NAME_PATTERN)
    trash_days: int = Field(default=7, ge=0, le=365)
    storage: DiskStorageIn | None = None
    access: list[AccessEntry] = Field(default_factory=list, max_length=500)


class UpdateDiskRequest(CamelModel):
    """Trash days (through `juicefs config`) and the cache policy (at the next mount)."""

    trash_days: int | None = Field(default=None, ge=0, le=365)
    cache: CachePolicy | None = None


def disk_name_taken() -> ApiError:
    """409: a live disk has the same name, compared case-insensitively as macOS does."""
    return ApiError(409, "disk_name_taken", "Name already in use")


def bucket_taken() -> ApiError:
    """409: another disk uses this bucket; each disk has its own (DECISIONS #23)."""
    return ApiError(409, "bucket_taken", "Another disk uses this bucket", {"field": "bucket"})


def format_failed(message: str) -> ApiError:
    """502: `juicefs config` failed; nothing changed."""
    return ApiError(502, "format_failed", "JuiceFS refused the change", {"reason": message[:300]})


def mount_path(name: str) -> str:
    """Where a disk appears on every device: a folder of the one share."""
    return f"{MOUNT_ROOT}/{name}"


def managed_bucket(volume_id: UUID) -> str:
    """The bucket the adapter creates for a disk: unique forever with the disk's id."""
    return f"floppy-{volume_id.hex}"


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
        select(Volume, VolumeStatus, User, MetaInstance.url, mounted)
        .outerjoin(
            VolumeStatus,
            (VolumeStatus.volume_id == Volume.id)
            & (VolumeStatus.organization_id == Volume.organization_id),
        )
        .outerjoin(
            MetaInstance,
            (MetaInstance.volume_id == Volume.id)
            & (MetaInstance.organization_id == Volume.organization_id)
            & (MetaInstance.state == "ready"),
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
            meta_url=device_meta_url(url) if url else None,
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
        for v, status, creator, url, count in rows
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


def _storage_of(org: Organization, body: CreateDiskRequest) -> tuple[str, str, str | None]:
    """The new disk's endpoint, region and, manual, bucket; the request fits the mode."""
    if org.storage_mode == "managed":
        if body.storage is not None:
            raise validation(
                "The provider creates each disk's bucket: send no storage", {"field": "storage"}
            )
        return org.s3_endpoint, org.s3_region, None
    if body.storage is None:
        raise validation("Each disk has its own bucket and key: paste them", {"field": "storage"})
    storage = body.storage
    return storage.endpoint or org.s3_endpoint, storage.region or org.s3_region, storage.bucket


async def create_disk(ctx: Context, actor: Actor, body: CreateDiskRequest) -> DiskOut:
    """Records a new disk as `creating` (manual: its pasted key probed first); the job does the rest."""
    async with ctx.sessions() as db:
        org_now = await db.get(Organization, actor.organization_id)
    if org_now is None:
        raise not_found()
    endpoint, region, bucket = _storage_of(org_now, body)
    pasted: S3Credentials | None = None
    probed: ProbeResult | None = None
    if body.storage is not None and bucket is not None:
        pasted = S3Credentials(body.storage.access_key_id.strip(), body.storage.secret_key)
        result = await probe(
            ctx, endpoint=endpoint, region=region, bucket=bucket, key=pasted, label="disk"
        )
        if not result.ok:
            raise probe_failed(result.code, result.request)
        probed = result
    who = AuditActor.person(actor.user_id, actor.label)
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, actor.organization_id)
        org = await db.get(Organization, actor.organization_id)
        if org is None:
            raise not_found()
        if await db.scalar(
            select(func.count()).where(
                Volume.organization_id == org.id,
                func.lower(Volume.name) == body.name.lower(),
                Volume.deleted_at.is_(None),
            )
        ):
            raise disk_name_taken()
        if org.storage_mode == "managed" and await control_plane_key(db, org.id) is None:
            raise invalid_state("The organization has no control-plane key")
        volume_id = uuid7()
        bucket = bucket or managed_bucket(volume_id)
        if await db.scalar(
            select(func.count()).where(
                Volume.s3_endpoint == endpoint, Volume.s3_bucket == bucket, Volume.state != "failed"
            )
        ):
            raise bucket_taken()
        people = await _access_entries(db, org.id, actor, body.access)
        taken = set(
            (
                await db.execute(select(Volume.volume_name).where(Volume.organization_id == org.id))
            ).scalars()
        )
        volume = Volume(
            id=volume_id,
            organization_id=org.id,
            name=body.name,
            volume_name=derive_volume_name(body.name, taken),
            s3_endpoint=endpoint,
            s3_region=region,
            s3_bucket=bucket,
            block_size_kb=BLOCK_SIZE_KB,
            trash_days=body.trash_days,
            created_by_user_id=actor.user_id,
        )
        db.add(volume)
        await db.flush()
        status = VolumeStatus(volume_id=volume.id, organization_id=org.id)
        if probed is not None:
            # The write with the pasted key just succeeded: the disk's storage is tested.
            status.storage_state, status.storage_probed_at = "ok", utcnow()
            status.bucket_versioning = probed.versioning
            status.bucket_old_versions_days = probed.old_versions_days
        db.add(status)
        record(
            db, org.id, "disk_created", who, Target("disk", volume.id, body.name),
            {"blockSize": "1 MB"},
        )  # fmt: skip
        if pasted is not None:
            await _add_disk_key(ctx, db, volume, pasted, actor)
        for membership_id, (access, label) in people.items():
            await put_access(
                db, org.id, volume, membership_id, access, actor=who, person_label=label
            )
    ctx.disk_wakeup.set()
    return await get_disk(ctx, actor.organization_id, volume_id)


async def _access_entries(
    db: AsyncSession, organization_id: UUID, actor: Actor, entries: list[AccessEntry]
) -> dict[UUID, tuple[Literal["none", "read", "write"], str]]:
    """The access a new disk starts with, by membership: the listed people, the creator `write`."""
    rows = (
        await db.execute(
            select(Membership.id, Membership.user_id, User.name, User.email)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id == organization_id,
                Membership.status != "removed",
                Membership.deleted_at.is_(None),
            )
        )
    ).all()
    by_user = {user_id: (mid, name or email) for mid, user_id, name, email in rows}
    out: dict[UUID, tuple[Literal["none", "read", "write"], str]] = {}
    for entry in entries:
        if entry.person_id not in by_user:
            raise validation(
                "No such person", {"field": "access", "personId": str(entry.person_id)}
            )
        membership_id, label = by_user[entry.person_id]
        out[membership_id] = (entry.access, label)
    if actor.membership_id not in out:
        out[actor.membership_id] = ("write", actor.label)
    return {m: v for m, v in out.items() if v[0] != "none"}


async def _add_disk_key(
    ctx: Context, db: AsyncSession, volume: Volume, key: S3Credentials, actor: Actor
) -> None:
    if await db.scalar(
        select(func.count()).where(
            S3Key.organization_id == volume.organization_id,
            S3Key.access_key_id == key.access_key_id,
            S3Key.state.in_(("pending", "active", "retiring")),
        )
    ):
        raise validation("This key already serves another disk", {"field": "accessKeyId"})
    row = new_key_row(
        ctx,
        volume.organization_id,
        key.access_key_id,
        key.secret_key,
        holder="disk",
        user_id=actor.user_id,
        volume_id=volume.id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    record(
        db,
        volume.organization_id,
        "key_created",
        AuditActor.person(actor.user_id, actor.label),
        Target("key", row.id, row.access_key_tail),
        {"holder": "disk", "accessKeyTail": row.access_key_tail, "for": volume.name},
    )


async def run_disk_formats(ctx: Context) -> int:
    """
    Creates every `creating` disk not taken by another process, one per transaction; the row
    stays locked while the meta service, the provider and JuiceFS run. Returns how many were
    handled.
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
            ready = await _create(ctx, db, volume)
            organization_id = volume.organization_id
        if ready:
            await apply_owed(ctx, organization_id)
        handled += 1


class _CreationError(Exception):
    """A step of a disk's creation failed; the message is the disk's `format_error`."""


async def _instance(ctx: Context, db: AsyncSession, volume: Volume) -> MetaInstance:
    """The disk's instance: the one recorded, or a new one from the meta service."""
    existing = await instance_of(db, volume.id)
    if existing is not None:
        return existing
    try:
        spec = await ctx.meta.create_instance(str(volume.id))
    except MetaServiceError as error:
        raise _CreationError(f"meta_unavailable: {error}") from error
    instance = MetaInstance(
        id=uuid7(),
        organization_id=volume.organization_id,
        volume_id=volume.id,
        service_ref=spec.service_ref,
        url=spec.url,
        admin_url=spec.admin_url,
        admin_username=spec.admin_username,
    )
    if spec.admin_password is not None:
        instance.admin_password_enc = ctx.box.seal(
            spec.admin_password,
            table="meta_instances",
            column="admin_password_enc",
            row_id=instance.id,
        )
    db.add(instance)
    await db.flush()
    return instance


async def _create(ctx: Context, db: AsyncSession, volume: Volume) -> bool:
    """The creation job for one disk; returns whether it became `ready`."""
    org = await db.get(Organization, volume.organization_id)
    if org is None:
        raise RuntimeError(f"disk {volume.id} lost its organization")
    managed = org.storage_mode == "managed"
    key = await storage_key(db, org, volume)
    instance: MetaInstance | None = None
    try:
        if key is None:
            raise _CreationError("The disk has no key to format with")
        credentials = ctx.credentials(key)
        instance = await _instance(ctx, db, volume)
        if managed:
            await _managed_bucket(ctx, db, org, volume, key, credentials)
        juicefs_uuid = await ctx.formatter.format(
            ctx.volume_location(instance, volume),
            credentials,
            block_size_kb=volume.block_size_kb,
            trash_days=volume.trash_days,
            store_key=not managed,
        )
    except (_CreationError, FormatError) as error:
        log.warning("creation of a disk failed: %s", error)
        await _fail(ctx, db, volume, instance, str(error))
        return False
    volume.state = "ready"
    volume.juicefs_uuid = juicefs_uuid
    if not managed:
        key.state, key.activated_at = "active", utcnow()
    await db.flush()
    await sync(db, org.id, volume_ids=[volume.id])
    return True


async def _managed_bucket(
    ctx: Context,
    db: AsyncSession,
    org: Organization,
    volume: Volume,
    key: S3Key,
    credentials: S3Credentials,
) -> None:
    """Managed: the adapter creates the bucket and makes the control plane's key its owner."""
    if key.provider_ref is None:
        raise _CreationError("The control plane's key has no provider id")
    try:
        adapter = ctx.adapter_for(org)
        await adapter.ensure_bucket(volume.s3_bucket)
        await adapter.set_grant(key.provider_ref, volume.s3_bucket, "write", owner=True)
    except ProviderError as error:
        raise _CreationError(f"provider_failed: {error}") from error
    result = await probe(
        ctx,
        endpoint=volume.s3_endpoint,
        region=volume.s3_region,
        bucket=volume.s3_bucket,
        key=credentials,
        label="disk",
    )
    await record_probe(db, volume, result)
    if not result.ok:
        raise _CreationError(f"probe_failed: {result.code} on {result.request}")


async def _fail(
    ctx: Context, db: AsyncSession, volume: Volume, instance: MetaInstance | None, message: str
) -> None:
    """A failed creation: the disk soft deleted, its key rejected, its instance wiped."""
    now = utcnow()
    volume.state = "failed"
    volume.format_error = message[:500]
    volume.deleted_at = now
    for key in (
        await db.execute(
            select(S3Key).where(S3Key.volume_id == volume.id, S3Key.state == "pending")
        )
    ).scalars():
        key.state, key.rejected_at, key.secret_key_enc = "rejected", now, None
    if instance is not None:
        try:
            await ctx.meta.release_instance(instance.service_ref, discard=True)
        except MetaServiceError as error:
            log.warning("the instance of a failed disk was not wiped: %s", error)
        instance.state, instance.stopped_at = "stopped", now


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
        if volume is None:
            raise not_found("No such disk")
        if volume.state != "ready":
            raise invalid_state("The disk is not ready")
        changes: dict[str, DetailValue] = {}
        if body.trash_days is not None and body.trash_days != volume.trash_days:
            instance = await instance_of(db, volume.id)
            if instance is None:
                raise invalid_state("The disk has no instance")
            try:
                await ctx.formatter.set_trash_days(
                    ctx.volume_location(instance, volume), body.trash_days
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


async def delete_disk(ctx: Context, actor: Actor, volume_id: UUID) -> None:
    """
    Soft deletes a disk: it leaves every list and every device, whose users on its instance
    are deleted like a lowering to `none` (the app unmounts a disk no longer listed). The
    instance, the metadata and the objects stay, and so does the disk's key at the provider:
    deleting files is a separate act (docs/DATABASE_SCHEMA.md, "volumes").
    """
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, actor.organization_id)
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
        if volume is None:
            raise not_found("No such disk")
        if volume.state == "creating":
            raise invalid_state("The disk is still being created")
        volume.deleted_at = utcnow()
        record(
            db,
            actor.organization_id,
            "disk_deleted",
            AuditActor.person(actor.user_id, actor.label),
            Target("disk", volume.id, volume.name),
            {"bucket": volume.s3_bucket},
        )
        await db.flush()
        await sync(db, actor.organization_id, volume_ids=[volume.id])
    await apply_owed(ctx, actor.organization_id)
