"""
Background work: the rotation worker, the Valkey ACL reconciler, housekeeping of stuck rows,
and the status pollers behind `GET /health` and the disks' counters.

Each job is safe to run in several processes at once: the rotation worker takes rows with
`SKIP LOCKED`, and the per-organization jobs take a transaction-level advisory lock, so a
second process skips an organization the first is handling.

The reconciler is what makes revocation durable (docs/DATABASE_SCHEMA.md, "devices"): Valkey
keeps ACL users in its own configuration and does not replicate them, so after a restart, a
failover or a revocation made while it was down, the master's `floppy_d_*` users are made to
match the active Macs again, from the password hashes kept here.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update

from floppy_server.audit.service import AuditActor
from floppy_server.context import Context, utcnow
from floppy_server.disks.service import run_disk_formats
from floppy_server.infra.metadata import MetadataUnavailableError
from floppy_server.keys.service import run_rotations
from floppy_server.macs.service import revoke_rows
from floppy_server.models import (
    Device,
    Organization,
    OrganizationStatus,
    Volume,
    VolumeStatus,
    advisory_lock,
)

log = logging.getLogger(__name__)

PENDING_DEVICE_TTL = timedelta(hours=24)
CREATING_VOLUME_TTL = timedelta(minutes=15)


async def _organizations(ctx: Context) -> list[UUID]:
    async with ctx.sessions() as db:
        return list(
            (
                await db.execute(select(Organization.id).where(Organization.deleted_at.is_(None)))
            ).scalars()
        )


async def reconcile_acl(ctx: Context, organization_id: UUID) -> None:
    """Makes the organization's Valkey users match its active Macs, and records owed cleanups."""
    async with ctx.sessions() as db, db.begin():
        if not await advisory_lock(db, "acl", organization_id, wait=False):
            return
        org = await db.get(Organization, organization_id)
        if org is None:
            return
        devices = list(
            (
                await db.execute(
                    select(Device).where(
                        Device.organization_id == organization_id,
                        (Device.state == "active")
                        | ((Device.state == "revoked") & Device.valkey_user_deleted_at.is_(None)),
                    )
                )
            ).scalars()
        )
        wanted = {
            d.valkey_username: d.valkey_password_sha256
            for d in devices
            if d.state == "active" and d.valkey_password_sha256 is not None
        }
        owed = [d for d in devices if d.state == "revoked"]
        target = ctx.valkey_target(org)
        try:
            present = await ctx.metadata.device_users(target)
            for username, digest in wanted.items():
                if digest not in present.get(username, set()):
                    log.info("reconciler: recreating a Mac's Valkey user")
                    await ctx.metadata.set_device_user(target, username, digest)
            for username in present.keys() - wanted.keys():
                log.info("reconciler: deleting a Valkey user with no active Mac")
                await ctx.metadata.delete_device_user(target, username)
        except MetadataUnavailableError as error:
            log.warning("reconciler: Valkey unavailable for an organization: %s", error)
            return
        if owed:
            await db.execute(
                update(Device)
                .where(
                    Device.organization_id == organization_id, Device.id.in_([d.id for d in owed])
                )
                .values(valkey_user_deleted_at=utcnow())
            )


async def expire_stale_rows(ctx: Context) -> None:
    """
    Pending Macs that never registered within a day are revoked (`never_registered`), and disks stuck
    `creating` after a crash become `failed`, their database number and prefix burned.
    """
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        stale = list(
            (
                await db.execute(
                    select(Device)
                    .where(Device.state == "pending", Device.created_at < now - PENDING_DEVICE_TTL)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        revoke_rows(db, stale, reason="never_registered", actor=AuditActor.system(), now=now)
        for device in stale:
            # A pending Mac never had a Valkey user: there is nothing to clean up.
            device.valkey_user_deleted_at = now
        # A row the format worker holds is still being formatted: skip it.
        stuck = (
            (
                await db.execute(
                    select(Volume.id)
                    .where(
                        Volume.state == "creating", Volume.created_at < now - CREATING_VOLUME_TTL
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        if stuck:
            await db.execute(
                update(Volume)
                .where(Volume.id.in_(stuck))
                .values(state="failed", deleted_at=now, format_error="The format was interrupted")
            )


async def poll_status(ctx: Context, organization_id: UUID) -> None:
    """Pings the organization's Valkey and reads each disk's counters."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        if not await advisory_lock(db, "status", organization_id, wait=False):
            return
        org = await db.get(Organization, organization_id)
        if org is None:
            return
        target = ctx.valkey_target(org)
        try:
            latency: float | None = await ctx.metadata.ping(target)
        except MetadataUnavailableError:
            latency = None
        await db.execute(
            update(OrganizationStatus)
            .where(OrganizationStatus.organization_id == organization_id)
            .values(
                metadata_state="ok" if latency is not None else "down",
                metadata_latency_ms=round(latency) if latency is not None else None,
                metadata_checked_at=now,
            )
        )
        rows = (
            await db.execute(
                select(Volume, VolumeStatus)
                .join(
                    VolumeStatus,
                    (VolumeStatus.volume_id == Volume.id)
                    & (VolumeStatus.organization_id == Volume.organization_id),
                )
                .where(
                    Volume.organization_id == organization_id,
                    Volume.state == "ready",
                    Volume.deleted_at.is_(None),
                )
            )
        ).all()
        for volume, status in rows:
            status.checked_at = now
            if latency is None:
                status.reachable = False
                continue
            try:
                counters = await ctx.metadata.volume_counters(target, volume.meta_db)
            except MetadataUnavailableError:
                status.reachable = False
                continue
            if (counters.used_bytes, counters.file_count) != (status.used_bytes, status.file_count):
                status.last_write_at = now
            status.reachable = True
            status.used_bytes = counters.used_bytes
            status.file_count = counters.file_count


async def reconcile_all(ctx: Context) -> None:
    """One pass of housekeeping and the ACL reconciler over every organization."""
    await expire_stale_rows(ctx)
    for organization_id in await _organizations(ctx):
        await reconcile_acl(ctx, organization_id)


async def poll_all(ctx: Context) -> None:
    """One pass of the status pollers over every organization."""
    for organization_id in await _organizations(ctx):
        await poll_status(ctx, organization_id)


async def _forever(
    name: str,
    interval: float,
    job: Callable[[], Awaitable[object]],
    wakeup: asyncio.Event | None = None,
) -> None:
    while True:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background job %s failed; retrying in %.0f s", name, interval)
        if wakeup is None:
            await asyncio.sleep(interval)
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wakeup.wait(), timeout=interval)
        wakeup.clear()


def start(ctx: Context) -> list[asyncio.Task[None]]:
    """Starts the background loops; the caller cancels them at shutdown."""
    s = ctx.settings
    return [
        asyncio.create_task(
            _forever(
                "rotations",
                s.rotation_poll_seconds,
                lambda: run_rotations(ctx),
                ctx.rotation_wakeup,
            )
        ),
        asyncio.create_task(
            _forever(
                "disks", s.rotation_poll_seconds, lambda: run_disk_formats(ctx), ctx.disk_wakeup
            )
        ),
        asyncio.create_task(
            _forever("reconcile", s.reconcile_interval_seconds, lambda: reconcile_all(ctx))
        ),
        asyncio.create_task(_forever("status", s.status_interval_seconds, lambda: poll_all(ctx))),
    ]
