"""
Each disk's S3 key in manual mode, its risk window and its rotation (DECISIONS #18, #24), the
storage probe, and the key status page.

A rotation is a row that is also a job: `start_rotation` creates it under the organization's
lock, and `run_rotations` (the worker, at startup and on a wakeup) takes `applying` rows with
`FOR UPDATE SKIP LOCKED`, probes the new key on the disk's bucket, runs `juicefs config` on
the disk and moves the keys' states. A restart resumes a rotation instead of losing it. The
state machines are in docs/DATABASE_SCHEMA.md, "Disk key", "Rotation" and "Key window".
Managed mode has no rotation: keys are per device (`keys.managed`).
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.audit.service import AuditActor, DetailValue, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, invalid_state, not_found, validation
from floppy_server.infra.juicefs import FormatError, S3Credentials
from floppy_server.infra.storage import ProbeResult
from floppy_server.keys.managed import control_plane_key
from floppy_server.keys.schemas import (
    DeviceKeyOut,
    DiskKeyOut,
    KeyStatusOut,
    KeyWindow,
    KeyWindowReason,
    RotationDevices,
    RotationError,
    RotationOut,
    Versioning,
)
from floppy_server.models import (
    Device,
    DeviceStatus,
    KeyExposure,
    KeyRotation,
    MetaInstance,
    MetaUser,
    Organization,
    S3Key,
    User,
    Volume,
    VolumeStatus,
    lock_organization,
)

log = logging.getLogger(__name__)

CONNECTED_WITHIN = timedelta(minutes=2)
SHOWN_ROTATION_STATES = ("applying", "awaiting_old_key_deletion", "failed")


def rotation_in_progress() -> ApiError:
    """409: a rotation of this disk is applying; one at a time."""
    return ApiError(409, "rotation_in_progress", "A rotation is already running")


def probe_failed(code: str | None, request: str | None) -> ApiError:
    """422: the test write with the key failed."""
    return ApiError(
        422, "probe_failed", "The test write failed", {"code": code or "", "request": request or ""}
    )


async def disk_key(db: AsyncSession, volume_id: UUID) -> S3Key | None:
    """The key a manual disk's format holds: its one active key."""
    return (
        await db.execute(
            select(S3Key).where(
                S3Key.volume_id == volume_id, S3Key.holder == "disk", S3Key.state == "active"
            )
        )
    ).scalar_one_or_none()


async def storage_key(db: AsyncSession, org: Organization, volume: Volume) -> S3Key | None:
    """
    The key the control plane uses on a disk's bucket: the disk's own (manual; `pending`
    while the disk is created), or the control plane's (managed).
    """
    if org.storage_mode == "managed":
        return await control_plane_key(db, org.id)
    return (
        await db.execute(
            select(S3Key)
            .where(
                S3Key.volume_id == volume.id,
                S3Key.holder == "disk",
                S3Key.state.in_(
                    ("active", "pending") if volume.state == "creating" else ("active",)
                ),
            )
            .order_by(S3Key.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def new_key_row(
    ctx: Context,
    organization_id: UUID,
    access_key_id: str,
    secret: str,
    *,
    holder: str,
    user_id: UUID | None,
    volume_id: UUID | None = None,
) -> S3Key:
    """A new `pending` key row with its secret sealed."""
    key = S3Key(
        id=uuid7(),
        organization_id=organization_id,
        access_key_id=access_key_id,
        holder=holder,
        volume_id=volume_id,
        origin="pasted" if holder == "disk" else "provider",
        created_by_user_id=user_id,
    )
    ctx.seal_secret(key, secret)
    return key


async def instance_of(db: AsyncSession, volume_id: UUID) -> MetaInstance | None:
    """A disk's ready instance."""
    return (
        await db.execute(
            select(MetaInstance).where(
                MetaInstance.volume_id == volume_id, MetaInstance.state == "ready"
            )
        )
    ).scalar_one_or_none()


async def record_probe(db: AsyncSession, volume: Volume, result: ProbeResult) -> None:
    """Keeps what a probe of a disk's bucket saw, versioning included when it succeeded."""
    values: dict[str, object] = {
        "storage_state": "ok" if result.ok else "failing",
        "storage_probed_at": utcnow(),
    }
    if result.ok:
        values["bucket_versioning"] = result.versioning
        values["bucket_old_versions_days"] = result.old_versions_days
    await db.execute(
        update(VolumeStatus)
        .where(
            VolumeStatus.volume_id == volume.id,
            VolumeStatus.organization_id == volume.organization_id,
        )
        .values(**values)
    )


async def probe(
    ctx: Context, *, endpoint: str, region: str, bucket: str, key: S3Credentials, label: str
) -> ProbeResult:
    """A test write on a bucket with a key."""
    return await ctx.prober.probe(
        endpoint=endpoint, region=region, bucket=bucket, key=key, label=label
    )


async def probe_disk(ctx: Context, organization_id: UUID, volume_id: UUID) -> None:
    """A test write on a disk's bucket with the key the control plane uses there."""
    async with ctx.sessions() as db:
        org = await db.get(Organization, organization_id)
        volume = (
            await db.execute(
                select(Volume).where(
                    Volume.organization_id == organization_id,
                    Volume.id == volume_id,
                    Volume.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if org is None or volume is None:
            raise not_found("No such disk")
        key = await storage_key(db, org, volume)
    if key is None:
        raise invalid_state("The disk has no key to probe with")
    result = await probe(
        ctx,
        endpoint=volume.s3_endpoint,
        region=volume.s3_region,
        bucket=volume.s3_bucket,
        key=ctx.credentials(key),
        label="disk",
    )
    async with ctx.sessions() as db, db.begin():
        await record_probe(db, volume, result)
    if not result.ok:
        raise probe_failed(result.code, result.request)


async def old_key_tails(db: AsyncSession, rotation: KeyRotation) -> list[str]:
    """
    The disk's keys to delete at the provider once this rotation closes: the one it replaces
    and those still `retiring` from rotations it superseded.
    """
    rows = (
        await db.execute(
            select(S3Key.access_key_tail)
            .where(
                S3Key.organization_id == rotation.organization_id,
                S3Key.volume_id == rotation.volume_id,
                S3Key.id != rotation.new_key_id,
                (S3Key.id == rotation.old_key_id) | (S3Key.state == "retiring"),
            )
            .order_by(S3Key.created_at)
        )
    ).scalars()
    return list(dict.fromkeys(rows))


async def rotation_view(db: AsyncSession, rotation: KeyRotation) -> RotationOut:
    """The API shape of a rotation, with the devices that picked up the new key."""
    new_key = await db.get(S3Key, rotation.new_key_id)
    volume = await db.get(Volume, rotation.volume_id)
    new_tail = new_key.access_key_tail if new_key else ""
    updated = (
        await db.scalar(
            select(func.count())
            .select_from(DeviceStatus)
            .join(
                Device,
                (Device.id == DeviceStatus.device_id)
                & (Device.organization_id == DeviceStatus.organization_id),
            )
            .where(
                DeviceStatus.organization_id == rotation.organization_id,
                Device.state == "active",
                DeviceStatus.s3_access_key_tail == new_tail,
            )
        )
        or 0
    )
    error = None
    if rotation.probe_error_code:
        error = RotationError(
            step="probe", code=rotation.probe_error_code, request=rotation.probe_error_request or ""
        )
    elif rotation.config_state == "failed":
        error = RotationError(
            step="config",
            code="JuiceFSConfigFailed",
            request=f"juicefs config {volume.volume_name if volume else ''}".strip(),
        )
    state = rotation.state if rotation.state in SHOWN_ROTATION_STATES else "failed"
    return RotationOut(
        id=rotation.id,
        disk_id=rotation.volume_id,
        disk_name=volume.name if volume else "",
        state=state,
        new_key_tail=new_tail,
        old_key_tails=await old_key_tails(db, rotation),
        probe=rotation.probe_state,
        config=rotation.config_state,
        devices=RotationDevices(
            updated=min(updated, rotation.devices_total), total=rotation.devices_total
        ),
        error=error,
        started_at=rotation.started_at,
    )


async def _window(db: AsyncSession, volume_id: UUID) -> KeyWindow | None:
    exposures = list(
        (
            await db.execute(
                select(KeyExposure)
                .where(KeyExposure.volume_id == volume_id, KeyExposure.closed_at.is_(None))
                .order_by(KeyExposure.occurred_at)
            )
        ).scalars()
    )
    if not exposures:
        return None
    return KeyWindow(
        opened_at=exposures[0].occurred_at,
        reasons=[
            KeyWindowReason(id=e.id, kind=e.kind, label=e.label, at=e.occurred_at)
            for e in exposures
        ],
    )


async def disk_key_view(db: AsyncSession, org: Organization, volume: Volume) -> DiskKeyOut:
    """One disk's storage as the key page shows it."""
    manual = org.storage_mode == "manual"
    key = await disk_key(db, volume.id) if manual else None
    latest = (
        await db.execute(
            select(KeyRotation)
            .where(KeyRotation.volume_id == volume.id)
            .order_by(KeyRotation.started_at.desc(), KeyRotation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    rotation = (
        await rotation_view(db, latest)
        if latest and latest.state in SHOWN_ROTATION_STATES
        else None
    )
    rotated_at = await db.scalar(
        select(func.max(KeyRotation.applied_at)).where(KeyRotation.volume_id == volume.id)
    )
    status = await db.get(VolumeStatus, volume.id)
    return DiskKeyOut(
        disk_id=volume.id,
        disk_name=volume.name,
        bucket=volume.s3_bucket,
        endpoint=volume.s3_endpoint,
        access_key_tail=key.access_key_tail if key else None,
        rotated_at=rotated_at,
        window=await _window(db, volume.id) if manual else None,
        rotation=rotation,
        versioning=Versioning(
            state=status.bucket_versioning if status else "unknown",
            old_versions_days=status.bucket_old_versions_days if status else None,
        ),
    )


async def key_status(db: AsyncSession, org: Organization) -> KeyStatusOut:
    """Every live disk's key, window and rotation, and the devices' keys."""
    volumes = (
        await db.execute(
            select(Volume)
            .where(Volume.organization_id == org.id, Volume.deleted_at.is_(None))
            .order_by(Volume.created_at)
        )
    ).scalars()
    cp = await control_plane_key(db, org.id) if org.storage_mode == "managed" else None
    return KeyStatusOut(
        storage_mode=org.storage_mode,
        storage_provider=org.storage_provider,
        control_plane_key_tail=cp.access_key_tail if cp else None,
        disks=[await disk_key_view(db, org, v) for v in volumes],
        keys=await device_keys(db, org.id),
    )


async def device_keys(db: AsyncSession, organization_id: UUID) -> list[DeviceKeyOut]:
    """The devices' live and owed keys, with whose they are."""
    rows = (
        await db.execute(
            select(S3Key, Device.name, User.name, User.email)
            .join(
                Device,
                (Device.id == S3Key.device_id) & (Device.organization_id == S3Key.organization_id),
            )
            .join(User, User.id == Device.user_id)
            .where(
                S3Key.organization_id == organization_id,
                S3Key.holder == "device",
                S3Key.state.in_(("pending", "active", "retiring")),
            )
            .order_by(S3Key.created_at)
        )
    ).all()
    return [
        DeviceKeyOut(
            id=key.id,
            device_id=key.device_id,
            owner=f"{device_name} ({user_name or email})",
            access_key_tail=key.access_key_tail,
            state=key.state,
            activated_at=key.activated_at,
            retiring_at=key.retiring_at,
        )
        for key, device_name, user_name, email in rows
        if key.device_id is not None
    ]


async def connected_devices_seeing(
    db: AsyncSession, organization_id: UUID, volume_id: UUID, now: datetime
) -> int:
    """Active devices with a heartbeat in the last two minutes and a user on the disk."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(DeviceStatus)
            .join(
                Device,
                (Device.id == DeviceStatus.device_id)
                & (Device.organization_id == DeviceStatus.organization_id),
            )
            .join(
                MetaUser,
                (MetaUser.device_id == Device.id) & (MetaUser.volume_id == volume_id),
            )
            .where(
                DeviceStatus.organization_id == organization_id,
                Device.state == "active",
                MetaUser.access != "none",
                DeviceStatus.last_seen_at > now - CONNECTED_WITHIN,
            )
        )
        or 0
    )


async def start_rotation(
    ctx: Context,
    org_id: UUID,
    volume_id: UUID,
    *,
    user_id: UUID,
    user_label: str,
    key: S3Credentials,
) -> KeyRotation:
    """Starts a rotation of one disk's key with a pasted new key; the worker does the rest."""
    now = utcnow()
    access_key_id = key.access_key_id.strip()
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, org_id)
        org = await db.get(Organization, org_id)
        if org is None:
            raise not_found()
        if org.storage_mode != "manual":
            raise invalid_state("Managed storage gives each device its own key: nothing to rotate")
        volume = (
            await db.execute(
                select(Volume).where(
                    Volume.organization_id == org_id,
                    Volume.id == volume_id,
                    Volume.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if volume is None:
            raise not_found("No such disk")
        if volume.state != "ready":
            raise invalid_state("The disk is not ready")
        rotations = list(
            (
                await db.execute(
                    select(KeyRotation).where(
                        KeyRotation.volume_id == volume_id,
                        KeyRotation.state.in_(("applying", "awaiting_old_key_deletion")),
                    )
                )
            ).scalars()
        )
        if any(r.state == "applying" for r in rotations):
            raise rotation_in_progress()
        old = await disk_key(db, volume_id)
        if old is None:
            raise invalid_state("The disk has no active key")
        if old.access_key_id == access_key_id:
            raise validation("This key is already in use", {"field": "accessKeyId"})
        for awaiting in rotations:
            awaiting.state = "superseded"
            awaiting.superseded_at = now
        new = await _pasted_disk_key(ctx, db, volume, access_key_id, key.secret_key, user_id, now)
        rotation = KeyRotation(
            organization_id=org_id,
            volume_id=volume_id,
            old_key_id=old.id,
            new_key_id=new.id,
            devices_total=await connected_devices_seeing(db, org_id, volume_id, now),
            started_by_user_id=user_id,
            started_at=now,
        )
        db.add(rotation)
        await db.flush()
        await db.refresh(new)
        tails = await old_key_tails(db, rotation)
        detail: dict[str, DetailValue] = {
            "disk": volume.name,
            "newKeyTail": new.access_key_tail,
            "oldKeyTail": ", ".join(tails),
        }
        record(
            db,
            org_id,
            "key_rotation_started",
            AuditActor.person(user_id, user_label),
            Target("key", new.id, new.access_key_tail),
            detail,
        )
    ctx.rotation_wakeup.set()
    return rotation


async def _pasted_disk_key(
    ctx: Context,
    db: AsyncSession,
    volume: Volume,
    access_key_id: str,
    secret: str,
    user_id: UUID,
    now: datetime,
) -> S3Key:
    new = (
        await db.execute(
            select(S3Key).where(
                S3Key.volume_id == volume.id,
                S3Key.access_key_id == access_key_id,
                S3Key.state == "pending",
                S3Key.holder == "disk",
            )
        )
    ).scalar_one_or_none()
    if new is not None:
        # A rotation that failed at the config: the same key, configured again.
        ctx.seal_secret(new, secret)
        return new
    # A different key replaces a pending one left by a failed rotation.
    await db.execute(
        update(S3Key)
        .where(S3Key.volume_id == volume.id, S3Key.state == "pending", S3Key.holder == "disk")
        .values(state="rejected", rejected_at=now, secret_key_enc=None)
    )
    if await db.scalar(
        select(func.count()).where(
            S3Key.organization_id == volume.organization_id,
            S3Key.access_key_id == access_key_id,
            S3Key.state.in_(("pending", "active", "retiring")),
        )
    ):
        raise validation("This key already serves another disk", {"field": "accessKeyId"})
    new = new_key_row(
        ctx,
        volume.organization_id,
        access_key_id,
        secret,
        holder="disk",
        user_id=user_id,
        volume_id=volume.id,
    )
    db.add(new)
    await db.flush()
    return new


async def get_rotation(ctx: Context, org_id: UUID, rotation_id: UUID) -> RotationOut:
    """A rotation of the organization."""
    async with ctx.sessions() as db:
        rotation = (
            await db.execute(
                select(KeyRotation).where(
                    KeyRotation.id == rotation_id, KeyRotation.organization_id == org_id
                )
            )
        ).scalar_one_or_none()
        if rotation is None:
            raise not_found("No such rotation")
        return await rotation_view(db, rotation)


async def close_rotation(
    ctx: Context, org_id: UUID, rotation_id: UUID, user_id: UUID, user_label: str
) -> None:
    """
    The admin confirmed the disk's old key is deleted at the provider: every retiring key of
    the disk is retired (secret wiped), and its exposures that happened before this rotation
    started are closed. One that happened while it was applying stays open.
    """
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, org_id)
        rotation = (
            await db.execute(
                select(KeyRotation)
                .where(KeyRotation.id == rotation_id, KeyRotation.organization_id == org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if rotation is None:
            raise not_found("No such rotation")
        if rotation.state != "awaiting_old_key_deletion":
            raise invalid_state("Only a rotation waiting for the old key's deletion can be closed")
        volume = await db.get(Volume, rotation.volume_id)
        rotation.state = "closed"
        rotation.closed_at = now
        rotation.closed_by_user_id = user_id
        tail = ", ".join(await old_key_tails(db, rotation))
        await db.execute(
            update(S3Key)
            .where(S3Key.volume_id == rotation.volume_id, S3Key.state == "retiring")
            .values(state="retired", retired_at=now, secret_key_enc=None)
        )
        closed = await db.execute(
            update(KeyExposure)
            .where(
                KeyExposure.volume_id == rotation.volume_id,
                KeyExposure.closed_at.is_(None),
                KeyExposure.occurred_at < rotation.started_at,
            )
            .values(closed_at=now, closed_by_rotation_id=rotation.id)
            .returning(KeyExposure.id)
        )
        record(
            db,
            org_id,
            "key_window_closed",
            AuditActor.person(user_id, user_label),
            Target("key", rotation.old_key_id, tail),
            {
                "disk": volume.name if volume else "",
                "oldKeyTail": tail,
                "reasons": len(closed.all()),
            },
        )


# The worker.


async def run_rotations(ctx: Context) -> int:
    """
    Advances every `applying` rotation not taken by another process, one unit per
    transaction (the probe, the config, the switch) so progress is visible while it runs and
    a crash loses at most one unit. Returns how many units ran.
    """
    ran = 0
    while True:
        async with ctx.sessions() as db, db.begin():
            rotation = (
                await db.execute(
                    select(KeyRotation)
                    .where(KeyRotation.state == "applying")
                    .order_by(KeyRotation.started_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if rotation is None:
                return ran
            await _step(ctx, db, rotation)
            ran += 1


async def _fail(
    db: AsyncSession, rotation: KeyRotation, volume: Volume, *, step: str, code: str
) -> None:
    rotation.state = "failed"
    rotation.failed_at = utcnow()
    new = await db.get(S3Key, rotation.new_key_id)
    record(
        db,
        rotation.organization_id,
        "key_rotation_failed",
        AuditActor.system(),
        Target("key", rotation.new_key_id, new.access_key_tail if new else ""),
        {"disk": volume.name, "step": step, "code": code},
    )


async def _step(ctx: Context, db: AsyncSession, rotation: KeyRotation) -> None:
    """One unit of a rotation: the probe, else the config, else the switch to the new key."""
    volume = await db.get(Volume, rotation.volume_id)
    new_key = await db.get(S3Key, rotation.new_key_id)
    if volume is None or new_key is None:
        raise RuntimeError(f"rotation {rotation.id} lost its disk or key")
    try:
        credentials = ctx.credentials(new_key)
    except ValueError:
        await _fail(db, rotation, volume, step="probe", code="KeyWiped")
        return
    if rotation.probe_state != "done":
        await _probe(ctx, db, volume, rotation, new_key, credentials)
        return
    if rotation.config_state != "done":
        instance = await instance_of(db, volume.id)
        if instance is None:
            await _fail(db, rotation, volume, step="config", code="NoInstance")
            return
        rotation.config_state = "running"
        try:
            await ctx.formatter.set_key(ctx.volume_location(instance, volume), credentials)
        except FormatError as error:
            rotation.config_state = "failed"
            rotation.config_error = str(error)[:500]
            await _fail(db, rotation, volume, step="config", code="JuiceFSConfigFailed")
            return
        rotation.config_state = "done"
        return
    await _applied(db, volume, rotation, new_key)


async def _probe(
    ctx: Context,
    db: AsyncSession,
    volume: Volume,
    rotation: KeyRotation,
    new_key: S3Key,
    credentials: S3Credentials,
) -> None:
    """The test write with the new key; a refusal rejects the key and fails the rotation."""
    now = utcnow()
    result = await probe(
        ctx,
        endpoint=volume.s3_endpoint,
        region=volume.s3_region,
        bucket=volume.s3_bucket,
        key=credentials,
        label=str(rotation.id)[-4:],
    )
    await record_probe(db, volume, result)
    if result.ok:
        rotation.probe_state = "done"
        return
    rotation.probe_state = "failed"
    rotation.probe_error_code = result.code or "ProbeFailed"
    rotation.probe_error_request = result.request
    new_key.state = "rejected"
    new_key.rejected_at = now
    new_key.secret_key_enc = None
    await _fail(db, rotation, volume, step="probe", code=rotation.probe_error_code)


async def _applied(db: AsyncSession, volume: Volume, rotation: KeyRotation, new_key: S3Key) -> None:
    """The format holds the new key: it becomes active, the disk's old one retiring."""
    now = utcnow()
    await db.execute(
        update(S3Key)
        .where(S3Key.volume_id == volume.id, S3Key.holder == "disk", S3Key.state == "active")
        .values(state="retiring", retiring_at=now)
    )
    await db.flush()
    new_key.state = "active"
    new_key.activated_at = now
    rotation.state = "awaiting_old_key_deletion"
    rotation.applied_at = now
    record(
        db,
        volume.organization_id,
        "key_rotated",
        AuditActor.system(),
        Target("key", new_key.id, new_key.access_key_tail),
        {"disk": volume.name},
    )
