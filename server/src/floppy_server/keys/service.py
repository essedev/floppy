"""
The organization's S3 key, its risk window and its rotation (DECISIONS #4).

A rotation is a row that is also a job: `start_rotation` creates it under the organization's
lock, and `run_rotations` (the worker, at startup and on a wakeup) takes `applying` rows with
`FOR UPDATE SKIP LOCKED`, probes the new key, runs `juicefs config` on each disk and moves
the keys' states. A restart resumes a rotation instead of losing it. The state machines are
in docs/DATABASE_SCHEMA.md, "S3 key", "Rotation" and "Key window".
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, invalid_state, not_found, validation
from floppy_server.infra.juicefs import FormatError, S3Credentials
from floppy_server.keys.schemas import (
    KeyStatusOut,
    KeyWindow,
    KeyWindowReason,
    RotationDisk,
    RotationError,
    RotationMacs,
    RotationOut,
)
from floppy_server.models import (
    Device,
    DeviceStatus,
    KeyExposure,
    KeyRotation,
    KeyRotationVolume,
    Organization,
    OrganizationStatus,
    S3Key,
    Volume,
    lock_organization,
)

log = logging.getLogger(__name__)

CONNECTED_WITHIN = timedelta(minutes=2)
SHOWN_ROTATION_STATES = ("applying", "awaiting_old_key_deletion", "failed")


def rotation_in_progress() -> ApiError:
    """409: a rotation is applying; one at a time."""
    return ApiError(409, "rotation_in_progress", "A rotation is already running")


async def active_key(db: AsyncSession, organization_id: UUID) -> S3Key | None:
    """The key in use."""
    return (
        await db.execute(
            select(S3Key).where(S3Key.organization_id == organization_id, S3Key.state == "active")
        )
    ).scalar_one_or_none()


def seal_secret(ctx: Context, key: S3Key, secret: str) -> None:
    """Encrypts a key's secret into its row; the row needs its id first."""
    key.secret_key_enc = ctx.box.seal(
        secret, table="s3_keys", column="secret_key_enc", row_id=key.id
    )


def new_key_row(
    ctx: Context, organization_id: UUID, access_key_id: str, secret: str, user_id: UUID | None
) -> S3Key:
    """A new `pending` key row with its secret sealed."""
    key = S3Key(
        id=uuid7(),
        organization_id=organization_id,
        access_key_id=access_key_id,
        created_by_user_id=user_id,
    )
    seal_secret(ctx, key, secret)
    return key


def open_exposure(
    db: AsyncSession,
    organization_id: UUID,
    *,
    kind: str,
    label: str,
    membership_id: UUID | None = None,
    device_id: UUID | None = None,
) -> None:
    """Opens the key window: someone who could read the key lost access."""
    db.add(
        KeyExposure(
            organization_id=organization_id,
            kind=kind,
            label=label,
            membership_id=membership_id,
            device_id=device_id,
            occurred_at=utcnow(),
        )
    )


async def old_key_tails(db: AsyncSession, rotation: KeyRotation) -> list[str]:
    """
    The keys to delete at the provider once this rotation closes: the one it replaces and
    those still `retiring` from rotations it superseded.
    """
    rows = (
        await db.execute(
            select(S3Key.access_key_tail)
            .where(
                S3Key.organization_id == rotation.organization_id,
                S3Key.id != rotation.new_key_id,
                (S3Key.id == rotation.old_key_id) | (S3Key.state == "retiring"),
            )
            .order_by(S3Key.created_at)
        )
    ).scalars()
    return list(dict.fromkeys(rows))


async def rotation_view(db: AsyncSession, rotation: KeyRotation) -> RotationOut:
    """The API shape of a rotation, with its steps and the Macs that picked up the new key."""
    org_id = rotation.organization_id
    new_key = await db.get(S3Key, rotation.new_key_id)
    steps = (
        await db.execute(
            select(KeyRotationVolume, Volume)
            .join(
                Volume,
                (Volume.id == KeyRotationVolume.volume_id)
                & (Volume.organization_id == KeyRotationVolume.organization_id),
            )
            .where(
                KeyRotationVolume.rotation_id == rotation.id,
                KeyRotationVolume.organization_id == org_id,
            )
            .order_by(Volume.created_at)
        )
    ).all()
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
                DeviceStatus.organization_id == org_id,
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
    else:
        failed = next((volume for step, volume in steps if step.state == "failed"), None)
        if failed is not None:
            error = RotationError(
                step="disk",
                code="JuiceFSConfigFailed",
                request=f"juicefs config {failed.volume_name}",
            )
    state = rotation.state if rotation.state in SHOWN_ROTATION_STATES else "failed"
    return RotationOut(
        id=rotation.id,
        state=state,
        new_key_tail=new_tail,
        old_key_tails=await old_key_tails(db, rotation),
        probe=rotation.probe_state,
        disks=[
            RotationDisk(id=volume.id, name=volume.name, state=step.state) for step, volume in steps
        ],
        macs=RotationMacs(updated=min(updated, rotation.macs_total), total=rotation.macs_total),
        error=error,
        started_at=rotation.started_at,
    )


async def key_status(db: AsyncSession, org: Organization) -> KeyStatusOut:
    """The key in use, the window and the rotation to show."""
    key = await active_key(db, org.id)
    exposures = list(
        (
            await db.execute(
                select(KeyExposure)
                .where(KeyExposure.organization_id == org.id, KeyExposure.closed_at.is_(None))
                .order_by(KeyExposure.occurred_at)
            )
        ).scalars()
    )
    window = None
    if exposures:
        window = KeyWindow(
            opened_at=exposures[0].occurred_at,
            reasons=[
                KeyWindowReason(id=e.id, kind=e.kind, label=e.label, at=e.occurred_at)
                for e in exposures
            ],
        )
    latest = (
        await db.execute(
            select(KeyRotation)
            .where(KeyRotation.organization_id == org.id)
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
        select(func.max(KeyRotation.applied_at)).where(KeyRotation.organization_id == org.id)
    )
    return KeyStatusOut(
        bucket=org.s3_bucket,
        access_key_tail=key.access_key_tail if key else "",
        rotated_at=rotated_at,
        window=window,
        rotation=rotation,
    )


async def connected_macs(db: AsyncSession, organization_id: UUID, now: datetime) -> int:
    """Active Macs with a heartbeat in the last two minutes."""
    return int(
        await db.scalar(
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
            )
        )
        or 0
    )


async def start_rotation(
    ctx: Context, org_id: UUID, user_id: UUID, user_label: str, access_key_id: str, secret: str
) -> KeyRotation:
    """Starts a rotation to a new key: one step per ready disk; the worker does the rest."""
    now = utcnow()
    access_key_id = access_key_id.strip()
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, org_id)
        rotations = list(
            (
                await db.execute(
                    select(KeyRotation).where(
                        KeyRotation.organization_id == org_id,
                        KeyRotation.state.in_(("applying", "awaiting_old_key_deletion")),
                    )
                )
            ).scalars()
        )
        if any(r.state == "applying" for r in rotations):
            raise rotation_in_progress()
        creating = await db.scalar(
            select(func.count()).where(
                Volume.organization_id == org_id,
                Volume.state == "creating",
                Volume.deleted_at.is_(None),
            )
        )
        if creating:
            raise invalid_state("A disk is being formatted; start the rotation when it is ready")
        old = await active_key(db, org_id)
        if old is None:
            raise invalid_state("The organization has no active key")
        if old.access_key_id == access_key_id:
            raise validation("This key is already in use", {"field": "accessKeyId"})
        for awaiting in rotations:
            awaiting.state = "superseded"
            awaiting.superseded_at = now
        new = (
            await db.execute(
                select(S3Key).where(
                    S3Key.organization_id == org_id,
                    S3Key.access_key_id == access_key_id,
                    S3Key.state == "pending",
                )
            )
        ).scalar_one_or_none()
        if new is not None:
            # A rotation that failed after some disks moved: the same key, configured again.
            seal_secret(ctx, new, secret)
        else:
            # A different key replaces a pending one left by a failed rotation.
            await db.execute(
                update(S3Key)
                .where(S3Key.organization_id == org_id, S3Key.state == "pending")
                .values(state="rejected", rejected_at=now, secret_key_enc=None)
            )
            new = new_key_row(ctx, org_id, access_key_id, secret, user_id)
            db.add(new)
            await db.flush()
        rotation = KeyRotation(
            organization_id=org_id,
            old_key_id=old.id,
            new_key_id=new.id,
            macs_total=await connected_macs(db, org_id, now),
            started_by_user_id=user_id,
            started_at=now,
        )
        db.add(rotation)
        await db.flush()
        volumes = (
            await db.execute(
                select(Volume.id).where(
                    Volume.organization_id == org_id,
                    Volume.state == "ready",
                    Volume.deleted_at.is_(None),
                )
            )
        ).scalars()
        for volume_id in volumes:
            db.add(
                KeyRotationVolume(
                    rotation_id=rotation.id, volume_id=volume_id, organization_id=org_id
                )
            )
        await db.refresh(new)
        tails = await old_key_tails(db, rotation)
        record(
            db,
            org_id,
            "key_rotation_started",
            AuditActor.person(user_id, user_label),
            Target("key", new.id, new.access_key_tail),
            {"newKeyTail": new.access_key_tail, "oldKeyTail": ", ".join(tails)},
        )
    ctx.rotation_wakeup.set()
    return rotation


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
    The admin confirmed the old key is deleted at the provider: every retiring key is
    retired (secret wiped), and the exposures that happened before this rotation started
    are closed. One that happened while it was applying stays open.
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
        rotation.state = "closed"
        rotation.closed_at = now
        rotation.closed_by_user_id = user_id
        retired = (
            await db.execute(
                update(S3Key)
                .where(S3Key.organization_id == org_id, S3Key.state == "retiring")
                .values(state="retired", retired_at=now, secret_key_enc=None)
                .returning(S3Key.access_key_tail, S3Key.created_at)
            )
        ).all()
        closed = await db.execute(
            update(KeyExposure)
            .where(
                KeyExposure.organization_id == org_id,
                KeyExposure.closed_at.is_(None),
                KeyExposure.occurred_at < rotation.started_at,
            )
            .values(closed_at=now, closed_by_rotation_id=rotation.id)
            .returning(KeyExposure.id)
        )
        reasons = len(closed.all())
        tail = ", ".join(t for t, _ in sorted(retired, key=lambda row: row[1]))
        record(
            db,
            org_id,
            "key_window_closed",
            AuditActor.person(user_id, user_label),
            Target("key", rotation.old_key_id, tail),
            {"oldKeyTail": tail, "reasons": reasons},
        )


# The worker.


async def run_rotations(ctx: Context) -> int:
    """
    Advances every `applying` rotation not taken by another process, one unit per
    transaction (the probe, one disk, the final switch) so progress is visible while it
    runs and a crash loses at most one unit. Returns how many units ran.
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
    db: AsyncSession, rotation: KeyRotation, *, step: str, code: str, now: datetime
) -> None:
    rotation.state = "failed"
    rotation.failed_at = now
    new = await db.get(S3Key, rotation.new_key_id)
    record(
        db,
        rotation.organization_id,
        "key_rotation_failed",
        AuditActor.system(),
        Target("key", rotation.new_key_id, new.access_key_tail if new else ""),
        {"step": step, "code": code},
    )


async def _step(ctx: Context, db: AsyncSession, rotation: KeyRotation) -> None:
    """One unit of a rotation: the probe, else the next disk, else the switch to the new key."""
    org = await db.get(Organization, rotation.organization_id)
    new_key = await db.get(S3Key, rotation.new_key_id)
    if org is None or new_key is None:
        raise RuntimeError(f"rotation {rotation.id} lost its organization or key")
    now = utcnow()
    try:
        credentials = ctx.credentials(new_key)
    except ValueError:
        await _fail(db, rotation, step="probe", code="KeyWiped", now=now)
        return
    if rotation.probe_state != "done":
        await _probe(ctx, db, org, rotation, new_key, credentials)
        return
    step = (
        await db.execute(
            select(KeyRotationVolume, Volume)
            .join(
                Volume,
                (Volume.id == KeyRotationVolume.volume_id)
                & (Volume.organization_id == KeyRotationVolume.organization_id),
            )
            .where(KeyRotationVolume.rotation_id == rotation.id, KeyRotationVolume.state != "done")
            .order_by(Volume.created_at)
            .limit(1)
        )
    ).one_or_none()
    if step is None:
        await _applied(db, org, rotation, new_key)
        return
    disk_step, volume = step
    disk_step.state = "running"
    disk_step.started_at = utcnow()
    try:
        await ctx.formatter.set_key(ctx.volume_location(org, volume), credentials)
    except FormatError as error:
        disk_step.state = "failed"
        disk_step.error = str(error)[:500]
        disk_step.finished_at = utcnow()
        await _fail(db, rotation, step="disk", code="JuiceFSConfigFailed", now=utcnow())
        return
    disk_step.state = "done"
    disk_step.finished_at = utcnow()
    volume.s3_key_id = new_key.id


async def _probe(
    ctx: Context,
    db: AsyncSession,
    org: Organization,
    rotation: KeyRotation,
    new_key: S3Key,
    credentials: S3Credentials,
) -> None:
    """The test write with the new key; a refusal rejects the key and fails the rotation."""
    now = utcnow()
    result = await ctx.prober.probe(
        endpoint=org.s3_endpoint,
        region=org.s3_region,
        bucket=org.s3_bucket,
        key=credentials,
        label=str(rotation.id)[-4:],
    )
    await db.execute(
        update(OrganizationStatus)
        .where(OrganizationStatus.organization_id == org.id)
        .values(storage_probed_at=now, storage_state="ok" if result.ok else "failing")
    )
    if result.ok:
        rotation.probe_state = "done"
        return
    rotation.probe_state = "failed"
    rotation.probe_error_code = result.code or "ProbeFailed"
    rotation.probe_error_request = result.request
    new_key.state = "rejected"
    new_key.rejected_at = now
    new_key.secret_key_enc = None
    await _fail(db, rotation, step="probe", code=rotation.probe_error_code, now=now)


async def _applied(
    db: AsyncSession, org: Organization, rotation: KeyRotation, new_key: S3Key
) -> None:
    """Every disk holds the new key: it becomes active, the old ones retiring."""
    now = utcnow()
    await db.execute(
        update(S3Key)
        .where(S3Key.organization_id == org.id, S3Key.state == "active")
        .values(state="retiring", retiring_at=now)
    )
    await db.flush()
    new_key.state = "active"
    new_key.activated_at = now
    rotation.state = "awaiting_old_key_deletion"
    rotation.applied_at = now
    disks = await db.scalar(
        select(func.count()).where(
            KeyRotationVolume.rotation_id == rotation.id, KeyRotationVolume.state == "done"
        )
    )
    record(
        db,
        org.id,
        "key_rotated",
        AuditActor.system(),
        Target("key", new_key.id, new_key.access_key_tail),
        {"disks": int(disks or 0)},
    )
