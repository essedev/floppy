"""
Background work: the disk creation and rotation workers, the reconciler of the devices' users
and grants, housekeeping of stuck rows, the status pollers behind `GET /health` and the
disks' counters, and the purge of the disks' activity after 30 days.

Each job is safe to run in several processes at once: the workers take rows with `SKIP
LOCKED`, and the per-organization jobs take a transaction-level advisory lock, so a second
process skips an organization the first is handling.

The reconciler is what makes revocation and access changes durable (docs/DATABASE_SCHEMA.md,
"meta_users"): it rewrites the wanted state from `disk_access`, applies what is owed, then
compares each disk's instance with it. Valkey keeps ACL users in its own configuration and
does not replicate them, so after a restart, a failover or a change made while an instance
was down, its `floppy_d_*` users are made to match again from the password hashes kept here.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update

from floppy_server.access.service import apply_owed, sync
from floppy_server.activity.service import purge
from floppy_server.audit.service import AuditActor
from floppy_server.context import Context, utcnow
from floppy_server.devices.service import revoke_rows
from floppy_server.disks.service import run_disk_formats
from floppy_server.infra.metadata import MetadataUnavailableError
from floppy_server.keys.managed import delete_owed_keys
from floppy_server.keys.service import run_rotations
from floppy_server.models import (
    Device,
    MetaInstance,
    MetaUser,
    Organization,
    Volume,
    VolumeStatus,
    advisory_lock,
)

log = logging.getLogger(__name__)

PENDING_DEVICE_TTL = timedelta(hours=24)
# Events past the 30 days go once an hour: nothing reads them by then.
ACTIVITY_PURGE_SECONDS = 3600.0
CREATING_VOLUME_TTL = timedelta(minutes=15)


async def _organizations(ctx: Context) -> list[UUID]:
    async with ctx.sessions() as db:
        return list(
            (
                await db.execute(select(Organization.id).where(Organization.deleted_at.is_(None)))
            ).scalars()
        )


async def reconcile_instances(ctx: Context, organization_id: UUID) -> None:
    """
    Makes every disk's instance of the organization match the wanted users: the wanted
    state rewritten from `disk_access`, the owed rows applied, then each instance compared
    with the rows and corrected.
    """
    async with ctx.sessions() as db, db.begin():
        if not await advisory_lock(db, "acl", organization_id, wait=False):
            return
        await sync(db, organization_id)
    await apply_owed(ctx, organization_id)
    async with ctx.sessions() as db:
        instances = list(
            (
                await db.execute(
                    select(MetaInstance)
                    .join(
                        Volume,
                        (Volume.id == MetaInstance.volume_id)
                        & (Volume.organization_id == MetaInstance.organization_id),
                    )
                    .where(
                        MetaInstance.organization_id == organization_id,
                        MetaInstance.state == "ready",
                        Volume.state == "ready",
                    )
                )
            ).scalars()
        )
    for instance in instances:
        await _correct(ctx, instance)


async def _correct(ctx: Context, instance: MetaInstance) -> None:
    """
    One instance made to match its applied rows: a missing or changed user set again, one with
    no row (or `none`) deleted. The rows are read after the instance, and owed rows are left
    to `apply_owed`, so a change made meanwhile is never undone by an older reading.
    """
    target = ctx.instance_target(instance)
    try:
        present = await ctx.metadata.device_users(target)
    except MetadataUnavailableError as error:
        log.warning("reconciler: a disk's instance is unavailable: %s", error)
        return
    async with ctx.sessions() as db:
        rows = (
            await db.execute(
                select(
                    Device.valkey_username,
                    Device.valkey_password_sha256,
                    Device.state,
                    MetaUser.access,
                    MetaUser.applied_at,
                    MetaUser.device_id,
                    MetaUser.updated_at,
                )
                .join(
                    Device,
                    (Device.id == MetaUser.device_id)
                    & (Device.organization_id == MetaUser.organization_id),
                )
                .where(
                    MetaUser.organization_id == instance.organization_id,
                    MetaUser.volume_id == instance.volume_id,
                )
            )
        ).all()
    owed = {r.valkey_username for r in rows if r.applied_at is None}
    seen = {r.device_id: r.updated_at for r in rows}
    wanted = {
        r.valkey_username: (r.valkey_password_sha256, r.access)
        for r in rows
        if r.applied_at is not None
        and r.access != "none"
        and r.state == "active"
        and r.valkey_password_sha256
    }
    touched = False
    try:
        for username, (digest, access) in wanted.items():
            user = present.get(username)
            if user is None or digest not in user.passwords or user.access != access:
                log.info("reconciler: setting a device's user on a disk's instance")
                touched = True
                await ctx.metadata.set_device_user(
                    target, username, digest, "write" if access == "write" else "read"
                )
        for username in present.keys() - wanted.keys() - owed:
            log.info("reconciler: deleting a user with no access on a disk's instance")
            touched = True
            await ctx.metadata.delete_device_user(target, username)
    except MetadataUnavailableError as error:
        log.warning("reconciler: a disk's instance is unavailable: %s", error)
    if touched:
        await _recheck(ctx, instance, seen)


async def _recheck(ctx: Context, instance: MetaInstance, seen: dict[UUID, datetime]) -> None:
    """
    A row changed while the sweep corrected its instance may have been overwritten by the
    older reading: it is marked owed again and applied, so the newer change wins.
    """
    async with ctx.sessions() as db, db.begin():
        now = (
            await db.execute(
                select(MetaUser.device_id, MetaUser.updated_at).where(
                    MetaUser.organization_id == instance.organization_id,
                    MetaUser.volume_id == instance.volume_id,
                )
            )
        ).all()
        changed = [device_id for device_id, at in now if seen.get(device_id) != at]
        if not changed:
            return
        await db.execute(
            update(MetaUser)
            .where(MetaUser.volume_id == instance.volume_id, MetaUser.device_id.in_(changed))
            .values(applied_at=None)
        )
    await apply_owed(ctx, instance.organization_id, device_ids=changed)


async def expire_stale_rows(ctx: Context) -> None:
    """
    Pending devices that never registered within a day are revoked (`never_registered`), and
    disks stuck `creating` after a crash become `failed`, their prefix burned.
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
        # A pending device never had a user or a key: there is nothing to clean up.
        revoke_rows(db, stale, reason="never_registered", actor=AuditActor.system(), now=now)
        # A row the creation worker holds is still being created: skip it.
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
                .values(state="failed", deleted_at=now, format_error="The creation was interrupted")
            )


async def poll_status(ctx: Context, organization_id: UUID) -> None:
    """Pings each disk's instance and reads its counters."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        if not await advisory_lock(db, "status", organization_id, wait=False):
            return
        rows = (
            await db.execute(
                select(Volume, VolumeStatus, MetaInstance)
                .join(
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
                .where(
                    Volume.organization_id == organization_id,
                    Volume.state == "ready",
                    Volume.deleted_at.is_(None),
                )
            )
        ).all()
        for _volume, status, instance in rows:
            status.checked_at = now
            if instance is None:
                status.reachable, status.meta_latency_ms = False, None
                continue
            target = ctx.instance_target(instance)
            try:
                latency = await ctx.metadata.ping(target)
                counters = await ctx.metadata.volume_counters(target)
            except MetadataUnavailableError:
                status.reachable, status.meta_latency_ms = False, None
                continue
            if (counters.used_bytes, counters.file_count) != (status.used_bytes, status.file_count):
                status.last_write_at = now
            status.reachable = True
            status.meta_latency_ms = round(latency)
            status.used_bytes = counters.used_bytes
            status.file_count = counters.file_count


async def reconcile_all(ctx: Context) -> None:
    """One pass of housekeeping and the reconciler over every organization."""
    await expire_stale_rows(ctx)
    for organization_id in await _organizations(ctx):
        await reconcile_instances(ctx, organization_id)
        await delete_owed_keys(ctx, organization_id)


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
        asyncio.create_task(_forever("activity", ACTIVITY_PURGE_SECONDS, lambda: purge(ctx))),
    ]
