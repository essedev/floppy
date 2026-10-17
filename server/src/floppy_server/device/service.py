"""
The app's side of a Mac: registration (its Valkey user), configuration, heartbeat and
sign-out, authenticated by the device token from the Keychain.

Enrolment (docs/DATABASE_SCHEMA.md, "Mac enrolment and revocation"): the token exchange
creates the device `pending`; registering creates its ACL user and turns it `active`, and
returns the Valkey password once. Registering again (Keychain lost) issues a new password.
The ACL user is created before the row commits: if the commit fails, the reconciler deletes
a user that matches no active Mac.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.api import CamelModel
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.crypto import token_hash, valkey_password
from floppy_server.disks.service import mount_path
from floppy_server.errors import ApiError, invalid_state, unauthorized
from floppy_server.infra.metadata import MetadataUnavailableError
from floppy_server.macs.service import delete_valkey_users, is_outdated, revoke_rows
from floppy_server.models import Device, DeviceStatus, Organization, User, Volume, advisory_lock

Version = Field(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z.+-]+$")


class RegisterRequest(CamelModel):
    """What the app reports when it registers."""

    app_version: str = Version
    macos_version: str = Version


class RegisterResponse(CamelModel):
    """The Mac's Valkey credentials, returned once, for the Keychain."""

    valkey_username: str
    valkey_password: str
    valkey_url: str


class DiskConfig(CamelModel):
    """A disk as the app mounts it."""

    id: UUID
    name: str
    volume_name: str
    meta_db: int
    cache_expire_days: int
    mount_path: str


class DeviceConfig(CamelModel):
    """Everything the app needs to mount: the Valkey address and the disks; no secret."""

    valkey_url: str
    disks: list[DiskConfig]


class ReplicaReport(CamelModel):
    """Where the Mac's JuiceFS reads metadata from, from the mount's `.stats`."""

    mode: Literal["replica", "master", "offline"]
    lag_ms: int | None = Field(default=None, ge=0)


class HeartbeatRequest(CamelModel):
    """What the Mac reports every 30-60 seconds."""

    app_version: str = Version
    macos_version: str = Version
    replica: ReplicaReport | None = None
    s3_access_key_tail: str | None = Field(default=None, max_length=5)
    mounted_disk_ids: list[UUID] = Field(default_factory=list, max_length=64)


class HeartbeatResponse(CamelModel):
    """`revoked` tells the app to stop the disk and clear its cache and replica."""

    state: Literal["active", "revoked"]


def metadata_unavailable() -> ApiError:
    """503: Valkey did not answer; nothing changed."""
    return ApiError(503, "metadata_unavailable", "The metadata server did not answer")


async def device_by_token(db: AsyncSession, token: str, *, for_update: bool = False) -> Device:
    """The device a token belongs to, or 401."""
    query = select(Device).where(
        Device.token_hash == token_hash(token), Device.deleted_at.is_(None)
    )
    if for_update:
        query = query.with_for_update()
    device = (await db.execute(query)).scalar_one_or_none()
    if device is None:
        raise unauthorized("Unknown device")
    return device


async def _owner_label(db: AsyncSession, device: Device) -> str:
    owner = await db.get(User, device.user_id)
    return (owner.name or owner.email) if owner else ""


async def _status(db: AsyncSession, device: Device, now: datetime, **values: object) -> None:
    """Writes the device's observed status, creating its row on the first report."""
    row = {"last_seen_at": now, "updated_at": now, **values}
    await db.execute(
        insert(DeviceStatus)
        .values(device_id=device.id, organization_id=device.organization_id, **row)
        .on_conflict_do_update(index_elements=[DeviceStatus.device_id], set_=row)
    )


async def register(ctx: Context, token: str, body: RegisterRequest) -> RegisterResponse:
    """Creates (or re-creates) the Mac's Valkey user and returns its password once."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await device_by_token(db, token, for_update=True)
        if device.state == "revoked":
            raise unauthorized("The Mac was revoked")
        org = await db.get(Organization, device.organization_id)
        if org is None or org.deleted_at is not None:
            raise unauthorized()
        # The reconciler must not see the new ACL user before this row is active.
        await advisory_lock(db, "acl", org.id, wait=True)
        password, digest = valkey_password()
        try:
            await ctx.metadata.set_device_user(
                ctx.valkey_target(org), device.valkey_username, digest
            )
        except MetadataUnavailableError as error:
            raise metadata_unavailable() from error
        reissued = device.state == "active"
        device.state = "active"
        device.valkey_password_sha256 = digest
        device.valkey_user_created_at = now
        device.activated_at = device.activated_at or now
        await _status(
            db, device, now, app_version=body.app_version, macos_version=body.macos_version
        )
        label = await _owner_label(db, device)
        detail: dict[str, str | int] = {
            "appVersion": body.app_version,
            "macosVersion": body.macos_version,
        }
        if reissued:
            detail["reissued"] = 1
        record(
            db,
            org.id,
            "mac_registered",
            AuditActor.person(device.user_id, label),
            Target("mac", device.id, device.name),
            detail,
        )
        return RegisterResponse(
            valkey_username=device.valkey_username,
            valkey_password=password,
            valkey_url=org.valkey_url,
        )


async def config(ctx: Context, token: str) -> DeviceConfig:
    """The Valkey address and the disks to mount, for an active Mac."""
    async with ctx.sessions() as db:
        device = await device_by_token(db, token)
        if device.state != "active":
            raise unauthorized("The Mac is not registered")
        org = await db.get(Organization, device.organization_id)
        if org is None:
            raise unauthorized()
        volumes = (
            await db.execute(
                select(Volume)
                .where(
                    Volume.organization_id == org.id,
                    Volume.state == "ready",
                    Volume.deleted_at.is_(None),
                )
                .order_by(Volume.created_at)
            )
        ).scalars()
        return DeviceConfig(
            valkey_url=org.valkey_url,
            disks=[
                DiskConfig(
                    id=v.id,
                    name=v.name,
                    volume_name=v.volume_name,
                    meta_db=v.meta_db,
                    cache_expire_days=v.cache_expire_days,
                    mount_path=mount_path(v.name),
                )
                for v in volumes
            ],
        )


async def heartbeat(ctx: Context, token: str, body: HeartbeatRequest) -> HeartbeatResponse:
    """Records what the Mac reports; answers `revoked` so a revoked Mac cleans up."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await device_by_token(db, token)
        if device.state != "active":
            if device.state == "revoked":
                return HeartbeatResponse(state="revoked")
            raise unauthorized("The Mac is not registered")
        await _status(
            db,
            device,
            now,
            app_version=body.app_version,
            macos_version=body.macos_version,
            replica_mode=body.replica.mode if body.replica else None,
            replica_lag_ms=body.replica.lag_ms if body.replica else None,
            s3_access_key_tail=body.s3_access_key_tail,
            mounted_volume_ids=body.mounted_disk_ids,
        )
        latest = ctx.settings.latest_app_version
        if is_outdated(body.app_version, latest):
            status = await db.get(DeviceStatus, device.id, with_for_update=True)
            if status is not None and status.outdated_logged_version != body.app_version:
                status.outdated_logged_version = body.app_version
                record(
                    db,
                    device.organization_id,
                    "mac_seen_outdated",
                    AuditActor.system(),
                    Target("mac", device.id, device.name),
                    {"appVersion": body.app_version},
                )
        return HeartbeatResponse(state="active")


async def sign_out(ctx: Context, token: str) -> None:
    """
    "Esci" in the app: the Mac is revoked by its owner. No key window: the Mac is in its
    owner's hands, wiped by the app, and its owner keeps access (DATABASE_SCHEMA.md, open
    question 7).
    """
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await device_by_token(db, token, for_update=True)
        if device.state == "revoked":
            raise invalid_state("The Mac is already signed out")
        label = await _owner_label(db, device)
        revoke_rows(
            db,
            [device],
            reason="signed_out",
            actor=AuditActor.person(device.user_id, label),
            now=now,
            audit_action="mac_signed_out",
        )
        organization_id, device_id = device.organization_id, device.id
    await delete_valkey_users(ctx, organization_id, [device_id])
