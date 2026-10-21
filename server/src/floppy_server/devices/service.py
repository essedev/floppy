"""
Devices: the list, revocation, and what a revocation owes outside PostgreSQL.

Revocation is durable before it is effective (docs/DATABASE_SCHEMA.md, "devices"): the
`revoked` state and the device's users set to `none` on every disk's instance are committed
first, then each user is deleted and its connections killed; an instance that does not answer
keeps its row owed for the reconciler. In manual mode each disk the device could see opens
its key window; in managed mode the device's own key is deleted at the provider.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from floppy_server.access.service import apply_owed, could_see, open_exposure, sync
from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, DetailValue, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import invalid_state, not_found
from floppy_server.infra.releases import version_tuple
from floppy_server.keys.managed import delete_owed_keys, retire_device_keys
from floppy_server.models import Device, DeviceStatus, Organization, User

log = logging.getLogger(__name__)

CONNECTED_WITHIN = timedelta(minutes=2)


class DeviceOwner(CamelModel):
    """Whose device it is."""

    id: UUID
    name: str


class Replica(CamelModel):
    """Where the device's JuiceFS reads metadata from, as its last heartbeat says."""

    mode: Literal["replica", "master", "offline"]
    lag_ms: int | None


class DeviceOut(CamelModel):
    """A device, as the admin web lists it."""

    id: UUID
    name: str
    state: Literal["pending", "active", "revoked"]
    owner: DeviceOwner
    valkey_user: str
    macos_version: str
    app_version: str
    app_outdated: bool
    last_seen_at: datetime | None
    connected: bool
    replica: Replica | None
    revoked_at: datetime | None
    revoked_by: str | None


def is_outdated(app_version: str | None, latest: str) -> bool:
    """Whether a device runs an app older than the latest release this instance knows."""
    return bool(app_version) and version_tuple(app_version or "") < version_tuple(latest)


def device_view(
    device: Device, owner: User, status: DeviceStatus | None, revoker: User | None, latest: str
) -> DeviceOut:
    """The API shape of a device row with its status."""
    now = utcnow()
    connected = (
        device.state != "revoked"
        and status is not None
        and status.last_seen_at > now - CONNECTED_WITHIN
    )
    replica = None
    if connected and status is not None and status.replica_mode is not None:
        replica = Replica(mode=status.replica_mode, lag_ms=status.replica_lag_ms)
    revoked_by = None
    if device.state == "revoked":
        revoked_by = (revoker.name or revoker.email) if revoker else None
    return DeviceOut(
        id=device.id,
        name=device.name,
        state=device.state,
        owner=DeviceOwner(id=owner.id, name=owner.name or owner.email),
        valkey_user=device.valkey_username,
        macos_version=status.macos_version if status else "",
        app_version=status.app_version if status else "",
        app_outdated=is_outdated(status.app_version if status else None, latest),
        last_seen_at=status.last_seen_at if status else None,
        connected=connected,
        replica=replica,
        revoked_at=device.revoked_at,
        revoked_by=revoked_by,
    )


async def list_devices(ctx: Context, actor: Actor) -> list[DeviceOut]:
    """Every device of the organization for an admin; only the caller's own for a member."""
    owner, revoker = aliased(User), aliased(User)
    query = (
        select(Device, owner, DeviceStatus, revoker)
        .join(owner, owner.id == Device.user_id)
        .outerjoin(
            DeviceStatus,
            (DeviceStatus.device_id == Device.id)
            & (DeviceStatus.organization_id == Device.organization_id),
        )
        .outerjoin(revoker, revoker.id == Device.revoked_by_user_id)
        .where(Device.organization_id == actor.organization_id, Device.deleted_at.is_(None))
    )
    if not actor.is_admin:
        query = query.where(Device.user_id == actor.user_id)
    async with ctx.sessions() as db:
        rows = (await db.execute(query)).all()
    devices = [device_view(d, o, s, r, ctx.latest_app_version) for d, o, s, r in rows]
    order = {"active": 0, "pending": 1, "revoked": 2}
    return sorted(devices, key=lambda d: (order[d.state], not d.connected, d.name.lower()))


async def get_device(ctx: Context, organization_id: UUID, device_id: UUID) -> DeviceOut:
    """One device with its status."""
    owner, revoker = aliased(User), aliased(User)
    async with ctx.sessions() as db:
        row = (
            await db.execute(
                select(Device, owner, DeviceStatus, revoker)
                .join(owner, owner.id == Device.user_id)
                .outerjoin(
                    DeviceStatus,
                    (DeviceStatus.device_id == Device.id)
                    & (DeviceStatus.organization_id == Device.organization_id),
                )
                .outerjoin(revoker, revoker.id == Device.revoked_by_user_id)
                .where(Device.organization_id == organization_id, Device.id == device_id)
            )
        ).one_or_none()
    if row is None:
        raise not_found("No such device")
    device, owner_row, status, revoker_row = row
    return device_view(device, owner_row, status, revoker_row, ctx.latest_app_version)


def revoke_rows(
    db: AsyncSession,
    devices: Sequence[Device],
    *,
    reason: str,
    actor: AuditActor,
    now: datetime,
    audit_action: str = "device_revoked",
) -> None:
    """Marks devices revoked and logs each."""
    for device in devices:
        device.state = "revoked"
        device.revoked_at = now
        device.revoke_reason = reason
        device.revoked_by_user_id = actor.user_id
        detail: dict[str, DetailValue] = (
            {"reason": reason} if audit_action == "device_revoked" else {}
        )
        record(
            db,
            device.organization_id,
            audit_action,
            actor,
            Target("device", device.id, device.name),
            detail,
        )


async def revoke_devices(
    db: AsyncSession,
    org: Organization,
    devices: Sequence[Device],
    *,
    reason: str,
    actor: AuditActor,
    now: datetime,
    audit_action: str = "device_revoked",
    person: tuple[UUID, str] | None = None,
) -> None:
    """
    Revokes devices in the caller's transaction, with what it owes outside: their users set to
    `none` on every instance, and the key window of each disk they could see (manual, one
    exposure per device, or one per disk for a person's removal given in `person`; none when
    the device only signed out) or their own keys retiring (managed). The caller applies the
    owed work after the commit (`finish_revocation`).
    """
    seen = await could_see(db, org.id, [d.id for d in devices])
    revoke_rows(db, devices, reason=reason, actor=actor, now=now, audit_action=audit_action)
    if org.storage_mode == "manual" and reason != "signed_out":
        for volume_id, device_ids in seen.items():
            if person is not None:
                open_exposure(
                    db, org.id, volume_id, kind="person_removed", label=person[1],
                    membership_id=person[0],
                )  # fmt: skip
                continue
            for device in devices:
                if device.id in device_ids:
                    open_exposure(
                        db, org.id, volume_id, kind="device_revoked", label=device.name,
                        device_id=device.id,
                    )  # fmt: skip
    if org.storage_mode == "managed":
        for device in devices:
            await retire_device_keys(db, org.id, device.id)
    await db.flush()
    await sync(db, org.id, device_ids=[d.id for d in devices])


async def finish_revocation(
    ctx: Context, organization_id: UUID, device_ids: Sequence[UUID]
) -> None:
    """After the commit: the users deleted on each instance, the keys at the provider."""
    if device_ids:
        await apply_owed(ctx, organization_id, device_ids=device_ids)
    await delete_owed_keys(ctx, organization_id)


async def revoke_device(ctx: Context, actor: Actor, device_id: UUID) -> None:
    """Revokes a device: an admin any, a member only their own (reason `owner`, a lost device)."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        query = select(Device).where(
            Device.organization_id == actor.organization_id,
            Device.id == device_id,
            Device.deleted_at.is_(None),
        )
        if not actor.is_admin:
            query = query.where(Device.user_id == actor.user_id)
        device = (await db.execute(query.with_for_update())).scalar_one_or_none()
        if device is None:
            raise not_found("No such device")
        if device.state == "revoked":
            raise invalid_state("The device is already revoked")
        reason = "admin" if actor.is_admin and device.user_id != actor.user_id else "owner"
        org = await db.get(Organization, actor.organization_id)
        if org is None:
            raise not_found()
        await revoke_devices(
            db,
            org,
            [device],
            reason=reason,
            actor=AuditActor.person(actor.user_id, actor.label),
            now=now,
        )
    await finish_revocation(ctx, actor.organization_id, [device_id])
