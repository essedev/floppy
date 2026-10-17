"""
Macs (devices): the list, revocation, and removing their Valkey users.

Revocation is durable before it is effective (docs/DATABASE_SCHEMA.md, "devices"): the
`revoked` state is committed first, then the Mac's ACL user is deleted and its connections
killed. When Valkey does not answer, `valkey_user_deleted_at` stays null and the reconciler
finishes the job; the Mac cannot write meanwhile only if Valkey is down too, which is the
same outage.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, DetailValue, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import invalid_state, not_found
from floppy_server.infra.metadata import MetadataUnavailableError
from floppy_server.keys.service import open_exposure
from floppy_server.models import Device, DeviceStatus, Organization, User

log = logging.getLogger(__name__)

CONNECTED_WITHIN = timedelta(minutes=2)


class MacOwner(CamelModel):
    """Whose Mac it is."""

    id: UUID
    name: str


class Replica(CamelModel):
    """Where the Mac's JuiceFS reads metadata from, as its last heartbeat says."""

    mode: Literal["replica", "master", "offline"]
    lag_ms: int | None


class MacOut(CamelModel):
    """A Mac, as the admin web lists it."""

    id: UUID
    name: str
    state: Literal["pending", "active", "revoked"]
    owner: MacOwner
    valkey_user: str
    macos_version: str
    app_version: str
    app_outdated: bool
    last_seen_at: datetime | None
    connected: bool
    replica: Replica | None
    revoked_at: datetime | None
    revoked_by: str | None


def version_tuple(version: str) -> tuple[int, ...]:
    """A dotted version as numbers, ignoring anything after the first non-numeric part."""
    parts: list[int] = []
    for piece in version.strip().lstrip("v").split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_outdated(app_version: str | None, latest: str) -> bool:
    """Whether a Mac runs an app older than the latest release this instance knows."""
    return bool(app_version) and version_tuple(app_version or "") < version_tuple(latest)


def mac_view(
    device: Device, owner: User, status: DeviceStatus | None, revoker: User | None, latest: str
) -> MacOut:
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
    return MacOut(
        id=device.id,
        name=device.name,
        state=device.state,
        owner=MacOwner(id=owner.id, name=owner.name or owner.email),
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


async def list_macs(ctx: Context, actor: Actor) -> list[MacOut]:
    """Every Mac of the organization for an admin; only the caller's own for a member."""
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
    macs = [mac_view(d, o, s, r, ctx.settings.latest_app_version) for d, o, s, r in rows]
    order = {"active": 0, "pending": 1, "revoked": 2}
    return sorted(macs, key=lambda m: (order[m.state], not m.connected, m.name.lower()))


async def get_mac(ctx: Context, organization_id: UUID, device_id: UUID) -> MacOut:
    """One Mac with its status."""
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
        raise not_found("No such Mac")
    device, owner_row, status, revoker_row = row
    return mac_view(device, owner_row, status, revoker_row, ctx.settings.latest_app_version)


def revoke_rows(
    db: AsyncSession,
    devices: Sequence[Device],
    *,
    reason: str,
    actor: AuditActor,
    now: datetime,
    audit_action: str = "mac_revoked",
) -> list[Device]:
    """
    Marks devices revoked and logs each; returns those that held Valkey credentials, which
    are the ones that could have read the S3 key.
    """
    exposed: list[Device] = []
    for device in devices:
        if device.state == "active" or device.valkey_password_sha256 is not None:
            exposed.append(device)
        device.state = "revoked"
        device.revoked_at = now
        device.revoke_reason = reason
        device.revoked_by_user_id = actor.user_id
        detail: dict[str, DetailValue] = {"reason": reason} if audit_action == "mac_revoked" else {}
        record(
            db,
            device.organization_id,
            audit_action,
            actor,
            Target("mac", device.id, device.name),
            detail,
        )
    return exposed


async def delete_valkey_users(
    ctx: Context, organization_id: UUID, device_ids: Sequence[UUID]
) -> None:
    """
    Deletes revoked Macs' ACL users and kills their connections, then records it. A Valkey
    that does not answer leaves the cleanup to the reconciler.
    """
    if not device_ids:
        return
    async with ctx.sessions() as db:
        org = await db.get(Organization, organization_id)
        devices = list(
            (
                await db.execute(
                    select(Device).where(
                        Device.organization_id == organization_id, Device.id.in_(device_ids)
                    )
                )
            ).scalars()
        )
    if org is None:
        return
    target = ctx.valkey_target(org)
    done: list[UUID] = []
    for device in devices:
        try:
            await ctx.metadata.delete_device_user(target, device.valkey_username)
        except MetadataUnavailableError as error:
            log.warning("Valkey cleanup of a revoked Mac postponed to the reconciler: %s", error)
            continue
        done.append(device.id)
    if done:
        async with ctx.sessions() as db, db.begin():
            await db.execute(
                update(Device)
                .where(Device.organization_id == organization_id, Device.id.in_(done))
                .values(valkey_user_deleted_at=utcnow())
            )


async def revoke_mac(ctx: Context, actor: Actor, device_id: UUID) -> None:
    """
    Revokes a Mac: an admin any, a member only their own (reason `owner`, a lost Mac). A Mac
    that held credentials opens the key window.
    """
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
            raise not_found("No such Mac")
        if device.state == "revoked":
            raise invalid_state("The Mac is already revoked")
        reason = "admin" if actor.is_admin and device.user_id != actor.user_id else "owner"
        exposed = revoke_rows(
            db,
            [device],
            reason=reason,
            actor=AuditActor.person(actor.user_id, actor.label),
            now=now,
        )
        if exposed:
            open_exposure(
                db,
                actor.organization_id,
                kind="mac_revoked",
                label=device.name,
                device_id=device.id,
            )
    await delete_valkey_users(ctx, actor.organization_id, [device_id])
