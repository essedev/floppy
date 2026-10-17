"""
The app's side of a device: registration (the bootstrap), configuration, heartbeat and
sign-out, authenticated by the device token from the Keychain.

Enrolment (docs/DATABASE_SCHEMA.md, "Device enrolment and revocation"): the token exchange
creates the device `pending`; registering turns it `active` with one Valkey password, returned
once, and gives it a user on the instance of every disk its person may see (with the ACL of
the access) and, in managed mode, its own S3 key granted on those buckets. The rows are
committed first and applied right after; what an instance does not take stays owed for the
reconciler. Registering again (Keychain lost) issues a new password and, managed, a new key.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.access.service import apply_owed, password_changed, sync, wanted
from floppy_server.api import CamelModel
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.crypto import token_hash, valkey_password
from floppy_server.devices.service import finish_revocation, is_outdated, revoke_devices
from floppy_server.disks.service import mount_path
from floppy_server.errors import device_unauthorized, invalid_state
from floppy_server.infra.meta import device_meta_url
from floppy_server.keys import managed
from floppy_server.models import (
    Device,
    DeviceStatus,
    MetaInstance,
    Organization,
    S3Key,
    User,
    Volume,
)

Version = Field(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z.+-]+$")
StorageMode = Literal["manual", "managed"]
Access = Literal["read", "write"]


class RegisterRequest(CamelModel):
    """What the app reports when it registers."""

    app_version: str = Version
    macos_version: str = Version


class S3KeyOut(CamelModel):
    """The device's own S3 key (managed), for the Keychain and the VM's 0600 file."""

    access_key_id: str
    secret_key: str


class OrganizationRef(CamelModel):
    """The organization the device belongs to, for the app to name it."""

    id: UUID
    name: str


class UserRef(CamelModel):
    """The person the device belongs to."""

    email: str
    name: str


class DiskConfig(CamelModel):
    """
    A disk the device may see, as the app mounts it: `metaUrl` is its own instance and
    database, without credentials (the app adds its user and password); `access: read` means
    a `--read-only` mount.
    """

    id: UUID
    name: str
    volume_name: str
    access: Access
    meta_url: str
    cache_expire_days: int
    mount_path: str


class RegisterResponse(CamelModel):
    """
    The bootstrap, returned once: the device's Valkey user and password (the same on every
    disk's instance), its own S3 key in managed mode (null in manual mode, where the key is
    in each disk's format), and the disks it may see.
    """

    valkey_username: str
    valkey_password: str
    storage_mode: StorageMode
    s3: S3KeyOut | None
    organization: OrganizationRef
    user: UserRef
    disks: list[DiskConfig]


class DeviceConfig(CamelModel):
    """
    Everything the app needs to mount, no secret. `s3AccessKeyTail` is the tail of the
    device's own key (managed), to check that the one in the Keychain is current.
    """

    storage_mode: StorageMode
    s3_access_key_tail: str | None
    organization: OrganizationRef
    user: UserRef
    disks: list[DiskConfig]


class ReplicaReport(CamelModel):
    """Where the device's JuiceFS reads metadata from, from the mounts' `.stats`."""

    mode: Literal["replica", "master", "offline"]
    lag_ms: int | None = Field(default=None, ge=0)


class HeartbeatRequest(CamelModel):
    """What the device reports every 30-60 seconds."""

    app_version: str = Version
    macos_version: str = Version
    replica: ReplicaReport | None = None
    s3_access_key_tail: str | None = Field(default=None, max_length=5)
    mounted_disk_ids: list[UUID] = Field(default_factory=list, max_length=64)


class DiskAccessRef(CamelModel):
    """A disk the device may see and how."""

    id: UUID
    access: Access


class HeartbeatResponse(CamelModel):
    """
    `revoked` tells the app to stop every disk and clear its cache and replicas. `disks` is
    what the device may see now: a disk mounted read-write whose access is `read` is remounted
    read-only, a mounted disk not listed is unmounted, a listed one not mounted is fetched
    from `GET /device/config` and mounted.
    """

    state: Literal["active", "revoked"]
    disks: list[DiskAccessRef]


def _refused(device: Device) -> None:
    """401 with the reason, for a device that is not active."""
    if device.state == "revoked":
        if device.revoke_reason == "signed_out":
            raise device_unauthorized("device_signed_out", "The device signed out")
        raise device_unauthorized("device_revoked", "The device was revoked")
    if device.state != "active":
        raise device_unauthorized("device_not_registered", "The device is not registered")


async def device_by_token(db: AsyncSession, token: str, *, for_update: bool = False) -> Device:
    """The device a token belongs to, or 401 `token_unknown`."""
    query = select(Device).where(
        Device.token_hash == token_hash(token), Device.deleted_at.is_(None)
    )
    if for_update:
        query = query.with_for_update()
    device = (await db.execute(query)).scalar_one_or_none()
    if device is None:
        raise device_unauthorized("token_unknown", "Unknown device")
    return device


async def _owner(db: AsyncSession, device: Device) -> User | None:
    return await db.get(User, device.user_id)


def _label(owner: User | None) -> str:
    return (owner.name or owner.email) if owner else ""


async def _status(db: AsyncSession, device: Device, now: datetime, **values: object) -> None:
    """Writes the device's observed status, creating its row on the first report."""
    row = {"last_seen_at": now, "updated_at": now, **values}
    await db.execute(
        insert(DeviceStatus)
        .values(device_id=device.id, organization_id=device.organization_id, **row)
        .on_conflict_do_update(index_elements=[DeviceStatus.device_id], set_=row)
    )


async def _disks(db: AsyncSession, device: Device) -> list[DiskConfig]:
    """The ready disks the device may see, with their access and instance."""
    access = {
        volume_id: level
        for (_, volume_id), level in (
            await wanted(db, device.organization_id, device_ids=[device.id])
        ).items()
        if level in ("read", "write")
    }
    if not access:
        return []
    rows = (
        await db.execute(
            select(Volume, MetaInstance.url)
            .join(
                MetaInstance,
                (MetaInstance.volume_id == Volume.id)
                & (MetaInstance.organization_id == Volume.organization_id)
                & (MetaInstance.state == "ready"),
            )
            .where(Volume.id.in_(access))
            .order_by(Volume.created_at)
        )
    ).all()
    return [
        DiskConfig(
            id=v.id,
            name=v.name,
            volume_name=v.volume_name,
            access="write" if access[v.id] == "write" else "read",
            meta_url=device_meta_url(url),
            cache_expire_days=v.cache_expire_days,
            mount_path=mount_path(v.name),
        )
        for v, url in rows
    ]


def _refs(org: Organization, owner: User | None) -> tuple[OrganizationRef, UserRef]:
    email = owner.email if owner else ""
    name = (owner.name or owner.email) if owner else ""
    return OrganizationRef(id=org.id, name=org.name), UserRef(email=email, name=name)


async def register(ctx: Context, token: str, body: RegisterRequest) -> RegisterResponse:
    """
    The bootstrap: a new password (and, managed, a new key) for the device, its users and
    grants on the disks it may see, returned once; then applied on each instance and at the
    provider. A reissue retires the previous key, which the adapter deletes.
    """
    registered, organization_id, device_id = await _register(ctx, token, body)
    await apply_owed(ctx, organization_id, device_ids=[device_id])
    if registered.storage_mode == "managed":
        await managed.delete_owed_keys(ctx, organization_id)
    return registered


async def _register(
    ctx: Context, token: str, body: RegisterRequest
) -> tuple[RegisterResponse, UUID, UUID]:
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await device_by_token(db, token, for_update=True)
        if device.state == "revoked":
            _refused(device)
        org = await db.get(Organization, device.organization_id)
        if org is None or org.deleted_at is not None:
            raise device_unauthorized("token_unknown", "Unknown device")
        password, digest = valkey_password()
        reissued = device.state == "active"
        device.state = "active"
        device.valkey_password_sha256 = digest
        device.valkey_user_created_at = now
        device.activated_at = device.activated_at or now
        await _status(
            db, device, now, app_version=body.app_version, macos_version=body.macos_version
        )
        owner = await _owner(db, device)
        detail: dict[str, str | int] = {
            "appVersion": body.app_version,
            "macosVersion": body.macos_version,
        }
        if reissued:
            detail["reissued"] = 1
        who = AuditActor.person(device.user_id, _label(owner))
        record(
            db, org.id, "device_registered", who, Target("device", device.id, device.name), detail
        )
        s3 = None
        if org.storage_mode == "managed":
            created = await managed.add_device_key(ctx, db, org, device, who)
            s3 = S3KeyOut(access_key_id=created.access_key_id, secret_key=created.secret_key)
        await db.flush()
        await password_changed(db, org.id, device.id)
        await sync(db, org.id, device_ids=[device.id])
        organization, user = _refs(org, owner)
        response = RegisterResponse(
            valkey_username=device.valkey_username,
            valkey_password=password,
            storage_mode=org.storage_mode,
            s3=s3,
            organization=organization,
            user=user,
            disks=await _disks(db, device),
        )
        return response, org.id, device.id


async def config(ctx: Context, token: str) -> DeviceConfig:
    """The disks an active device may see, with how to mount them."""
    async with ctx.sessions() as db:
        device = await device_by_token(db, token)
        _refused(device)
        org = await db.get(Organization, device.organization_id)
        if org is None:
            raise device_unauthorized("token_unknown", "Unknown device")
        tail = None
        if org.storage_mode == "managed":
            tail = (
                await db.execute(
                    select(S3Key.access_key_tail).where(
                        S3Key.organization_id == org.id,
                        S3Key.holder == "device",
                        S3Key.device_id == device.id,
                        S3Key.state == "active",
                    )
                )
            ).scalar_one_or_none()
        organization, user = _refs(org, await _owner(db, device))
        return DeviceConfig(
            storage_mode=org.storage_mode,
            s3_access_key_tail=tail,
            organization=organization,
            user=user,
            disks=await _disks(db, device),
        )


async def heartbeat(ctx: Context, token: str, body: HeartbeatRequest) -> HeartbeatResponse:
    """Records what the device reports; answers what it may see, or `revoked` to clean up."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await device_by_token(db, token)
        if device.state == "revoked":
            return HeartbeatResponse(state="revoked", disks=[])
        _refused(device)
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
                    "device_seen_outdated",
                    AuditActor.system(),
                    Target("device", device.id, device.name),
                    {"appVersion": body.app_version},
                )
        disks = [DiskAccessRef(id=d.id, access=d.access) for d in await _disks(db, device)]
        return HeartbeatResponse(state="active", disks=disks)


async def sign_out(ctx: Context, token: str) -> None:
    """
    "Esci" in the app: the device is revoked by its owner. No key window: the device is in its
    owner's hands, wiped by the app, and its owner keeps access (DATABASE_SCHEMA.md, decision
    7). Its users are deleted from every instance; managed, its key at the provider.
    """
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        device = await device_by_token(db, token, for_update=True)
        if device.state == "revoked":
            raise invalid_state("The device is already signed out")
        org = await db.get(Organization, device.organization_id)
        if org is None:
            raise device_unauthorized("token_unknown", "Unknown device")
        await revoke_devices(
            db,
            org,
            [device],
            reason="signed_out",
            actor=AuditActor.person(device.user_id, _label(await _owner(db, device))),
            now=now,
            audit_action="device_signed_out",
        )
        organization_id, device_id = device.organization_id, device.id
    await finish_revocation(ctx, organization_id, [device_id])
