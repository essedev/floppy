"""
Access per person and per disk (DECISIONS #23; docs/DATABASE_SCHEMA.md, "Access per disk").

`disk_access` says what each person may do on each disk. What that means on the outside is
derived from it: a Valkey user per (device, disk) with the ACL of the access (`meta_users`),
and in managed mode a grant per (device key, disk) at the provider (`s3_key_grants`). Both
are written as the wanted state with `applied_at` null in the same transaction as the change
(`sync`), then applied after the commit (`apply_owed`); what an instance or the provider did
not take stays owed for the reconciler. A change is durable before it is effective.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, not_found
from floppy_server.infra.metadata import MetadataUnavailableError
from floppy_server.infra.providers import Grant, ProviderError
from floppy_server.models import (
    Device,
    DiskAccess,
    KeyExposure,
    Membership,
    MetaInstance,
    MetaUser,
    Organization,
    S3Key,
    S3KeyGrant,
    User,
    Volume,
    lock_organization,
)

log = logging.getLogger(__name__)

AccessLevel = Literal["none", "read", "write"]
Pair = tuple[UUID, UUID]


class DiskAccessOut(CamelModel):
    """A person's access to a disk; `applied` is false while a device's user or grant is owed."""

    disk_id: UUID
    person_id: UUID
    access: AccessLevel
    applied: bool


class AccessEntry(CamelModel):
    """Access for one person, given when a disk is created."""

    person_id: UUID
    access: AccessLevel


class DiskEntry(CamelModel):
    """Access to one disk, given when a person is invited."""

    disk_id: UUID
    access: AccessLevel


class MyDiskOut(CamelModel):
    """A disk the signed-in person sees, with their access: what their start page lists."""

    id: UUID
    name: str
    access: Literal["read", "write"]


class SetAccessRequest(CamelModel):
    """The new access."""

    access: AccessLevel


def _grant(access: str) -> Grant:
    if access == "write":
        return "write"
    return "read" if access == "read" else "none"


async def wanted(
    db: AsyncSession,
    organization_id: UUID,
    *,
    device_ids: Sequence[UUID] | None = None,
    volume_ids: Sequence[UUID] | None = None,
) -> dict[Pair, AccessLevel]:
    """
    What each active device of an active person must have on each ready disk: its person's
    access, `none` when there is no row. Pairs of inactive devices or disks are not listed.
    """
    query = (
        select(Device.id, Volume.id, func.coalesce(DiskAccess.access, literal("none")))
        .join(
            Membership,
            (Membership.id == Device.membership_id)
            & (Membership.organization_id == Device.organization_id),
        )
        .join(Volume, Volume.organization_id == Device.organization_id)
        .outerjoin(
            DiskAccess,
            (DiskAccess.volume_id == Volume.id)
            & (DiskAccess.membership_id == Device.membership_id)
            & (DiskAccess.organization_id == Device.organization_id),
        )
        .where(
            Device.organization_id == organization_id,
            Device.state == "active",
            Membership.status == "active",
            Membership.deleted_at.is_(None),
            Volume.state == "ready",
            Volume.deleted_at.is_(None),
        )
    )
    if device_ids is not None:
        query = query.where(Device.id.in_(device_ids))
    if volume_ids is not None:
        query = query.where(Volume.id.in_(volume_ids))
    return {(d, v): _level(a) for d, v, a in (await db.execute(query)).all()}


def _level(value: str | None) -> AccessLevel:
    if value == "write":
        return "write"
    return "read" if value == "read" else "none"


async def sync(
    db: AsyncSession,
    organization_id: UUID,
    *,
    device_ids: Sequence[UUID] | None = None,
    volume_ids: Sequence[UUID] | None = None,
) -> int:
    """
    Writes the wanted `meta_users` and `s3_key_grants` of these devices and disks, marking
    owed every row whose access changes. Returns how many rows became owed.
    """
    want = await wanted(db, organization_id, device_ids=device_ids, volume_ids=volume_ids)
    owed = 0

    existing = select(MetaUser).where(MetaUser.organization_id == organization_id)
    if device_ids is not None:
        existing = existing.where(MetaUser.device_id.in_(device_ids))
    if volume_ids is not None:
        existing = existing.where(MetaUser.volume_id.in_(volume_ids))
    rows = {(r.device_id, r.volume_id): r for r in (await db.execute(existing)).scalars()}
    for pair in rows.keys() | {p for p, a in want.items() if a != "none"}:
        access = want.get(pair, "none")
        row = rows.get(pair)
        if row is None:
            db.add(
                MetaUser(
                    device_id=pair[0], volume_id=pair[1], organization_id=organization_id,
                    access=access,
                )
            )  # fmt: skip
            owed += 1
        elif row.access != access:
            row.access, row.applied_at = access, None
            owed += 1

    org = await db.get(Organization, organization_id)
    if org is not None and org.storage_mode == "managed":
        owed += await _sync_grants(db, organization_id, want, device_ids, volume_ids)
    await db.flush()
    return owed


async def _sync_grants(
    db: AsyncSession,
    organization_id: UUID,
    want: dict[Pair, AccessLevel],
    device_ids: Sequence[UUID] | None,
    volume_ids: Sequence[UUID] | None,
) -> int:
    keys_query = select(S3Key.id, S3Key.device_id).where(
        S3Key.organization_id == organization_id,
        S3Key.holder == "device",
        S3Key.state == "active",
    )
    if device_ids is not None:
        keys_query = keys_query.where(S3Key.device_id.in_(device_ids))
    keys: dict[UUID, UUID | None] = {}
    for key_id, device_id in (await db.execute(keys_query)).all():
        keys[key_id] = device_id
    if not keys:
        return 0
    existing = select(S3KeyGrant).where(
        S3KeyGrant.organization_id == organization_id, S3KeyGrant.key_id.in_(keys)
    )
    if volume_ids is not None:
        existing = existing.where(S3KeyGrant.volume_id.in_(volume_ids))
    rows = {(g.key_id, g.volume_id): g for g in (await db.execute(existing)).scalars()}
    wanted_grants = {
        (key_id, volume_id): access
        for key_id, device_id in keys.items()
        for (d, volume_id), access in want.items()
        if d == device_id and access != "none"
    }
    owed = 0
    for pair in rows.keys() | wanted_grants.keys():
        access = wanted_grants.get(pair, "none")
        row = rows.get(pair)
        if row is None:
            db.add(
                S3KeyGrant(
                    key_id=pair[0], volume_id=pair[1], organization_id=organization_id,
                    access=access,
                )
            )  # fmt: skip
            owed += 1
        elif row.access != access:
            row.access, row.applied_at = access, None
            owed += 1
    return owed


async def password_changed(db: AsyncSession, organization_id: UUID, device_id: UUID) -> None:
    """A device got a new password: every instance it has a user on must get the new hash."""
    await db.execute(
        update(MetaUser)
        .where(
            MetaUser.organization_id == organization_id,
            MetaUser.device_id == device_id,
            MetaUser.access != "none",
        )
        .values(applied_at=None)
    )


async def could_see(
    db: AsyncSession, organization_id: UUID, device_ids: Iterable[UUID]
) -> dict[UUID, list[UUID]]:
    """
    The disks each of these devices may have had a Valkey user on, by disk: every row except
    one whose `none` was applied. Read before a change, for the key window (manual mode).
    """
    ids = list(device_ids)
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(MetaUser.volume_id, MetaUser.device_id).where(
                MetaUser.organization_id == organization_id,
                MetaUser.device_id.in_(ids),
                ~((MetaUser.access == "none") & MetaUser.applied_at.is_not(None)),
            )
        )
    ).all()
    seen: dict[UUID, list[UUID]] = {}
    for volume_id, device_id in rows:
        seen.setdefault(volume_id, []).append(device_id)
    return seen


def open_exposure(
    db: AsyncSession,
    organization_id: UUID,
    volume_id: UUID,
    *,
    kind: str,
    label: str,
    membership_id: UUID | None = None,
    device_id: UUID | None = None,
) -> None:
    """Opens a disk's key window: someone who could read its key lost access to it."""
    db.add(
        KeyExposure(
            organization_id=organization_id,
            volume_id=volume_id,
            kind=kind,
            label=label,
            membership_id=membership_id,
            device_id=device_id,
            occurred_at=utcnow(),
        )
    )


@dataclass(frozen=True)
class _OwedUser:
    device_id: UUID
    volume_id: UUID
    access: str
    updated_at: datetime
    username: str
    password_sha256: bytes | None
    instance: MetaInstance | None


async def apply_owed(
    ctx: Context, organization_id: UUID, *, device_ids: Sequence[UUID] | None = None
) -> bool:
    """
    Applies the owed users on each instance and the owed grants at the provider; returns
    whether nothing is owed any more. What fails stays owed, for the reconciler.
    """
    users_done = await _apply_users(ctx, organization_id, device_ids)
    grants_done = await _apply_grants(ctx, organization_id, device_ids)
    return users_done and grants_done


async def _apply_users(
    ctx: Context, organization_id: UUID, device_ids: Sequence[UUID] | None
) -> bool:
    query = (
        select(MetaUser, Device.valkey_username, Device.valkey_password_sha256, MetaInstance)
        .join(
            Device,
            (Device.id == MetaUser.device_id)
            & (Device.organization_id == MetaUser.organization_id),
        )
        .outerjoin(
            MetaInstance,
            (MetaInstance.volume_id == MetaUser.volume_id)
            & (MetaInstance.organization_id == MetaUser.organization_id)
            & (MetaInstance.state == "ready"),
        )
        .where(MetaUser.organization_id == organization_id, MetaUser.applied_at.is_(None))
    )
    if device_ids is not None:
        query = query.where(MetaUser.device_id.in_(device_ids))
    async with ctx.sessions() as db:
        owed = [
            _OwedUser(m.device_id, m.volume_id, m.access, m.updated_at, name, digest, instance)
            for m, name, digest, instance in (await db.execute(query)).all()
        ]
    done: list[_OwedUser] = []
    for row in owed:
        if row.instance is None:
            # No instance: nothing to delete there; a user to create waits for one.
            if row.access == "none":
                done.append(row)
            continue
        target = ctx.instance_target(row.instance)
        try:
            if row.access == "none" or row.password_sha256 is None:
                await ctx.metadata.delete_device_user(target, row.username)
            else:
                await ctx.metadata.set_device_user(
                    target,
                    row.username,
                    row.password_sha256,
                    "write" if row.access == "write" else "read",
                )
        except MetadataUnavailableError as error:
            log.warning("a device's user on a disk's instance stays owed: %s", error)
            continue
        done.append(row)
    if done:
        async with ctx.sessions() as db, db.begin():
            now = utcnow()
            for row in done:
                # Only if nothing changed meanwhile: a newer change stays owed.
                await db.execute(
                    update(MetaUser)
                    .where(
                        MetaUser.device_id == row.device_id,
                        MetaUser.volume_id == row.volume_id,
                        MetaUser.access == row.access,
                        MetaUser.updated_at == row.updated_at,
                        MetaUser.applied_at.is_(None),
                    )
                    # updated_at says when the wanted state changed: applying keeps it.
                    .values(applied_at=now, updated_at=MetaUser.updated_at)
                )
    return len(done) == len(owed)


async def _apply_grants(
    ctx: Context, organization_id: UUID, device_ids: Sequence[UUID] | None
) -> bool:
    query = (
        select(S3KeyGrant, S3Key, Volume.s3_bucket)
        .join(
            S3Key,
            (S3Key.id == S3KeyGrant.key_id) & (S3Key.organization_id == S3KeyGrant.organization_id),
        )
        .join(
            Volume,
            (Volume.id == S3KeyGrant.volume_id)
            & (Volume.organization_id == S3KeyGrant.organization_id),
        )
        .where(S3KeyGrant.organization_id == organization_id, S3KeyGrant.applied_at.is_(None))
    )
    if device_ids is not None:
        query = query.where(S3Key.device_id.in_(device_ids))
    async with ctx.sessions() as db:
        org = await db.get(Organization, organization_id)
        owed = [
            (g.key_id, g.volume_id, g.access, g.updated_at, key.state, key.provider_ref, bucket)
            for g, key, bucket in (await db.execute(query)).all()
        ]
    if not owed or org is None:
        return True
    try:
        adapter = ctx.adapter_for(org)
    except ProviderError as error:
        log.warning("grants owed to the provider stay owed: %s", error)
        return False
    done = []
    for key_id, volume_id, access, updated_at, state, provider_ref, bucket in owed:
        # A key on its way out loses every grant when the provider deletes it.
        if state == "active" and provider_ref is not None:
            try:
                await adapter.set_grant(provider_ref, bucket, _grant(access))
            except ProviderError as error:
                log.warning("a key's grant on a disk's bucket stays owed: %s", error)
                continue
        done.append((key_id, volume_id, access, updated_at))
    if done:
        async with ctx.sessions() as db, db.begin():
            now = utcnow()
            for key_id, volume_id, access, updated_at in done:
                await db.execute(
                    update(S3KeyGrant)
                    .where(
                        S3KeyGrant.key_id == key_id,
                        S3KeyGrant.volume_id == volume_id,
                        S3KeyGrant.access == access,
                        S3KeyGrant.updated_at == updated_at,
                        S3KeyGrant.applied_at.is_(None),
                    )
                    .values(applied_at=now, updated_at=S3KeyGrant.updated_at)
                )
    return len(done) == len(owed)


async def put_access(
    db: AsyncSession,
    organization_id: UUID,
    volume: Volume,
    membership_id: UUID,
    access: AccessLevel,
    *,
    actor: AuditActor,
    person_label: str,
) -> AccessLevel:
    """Writes a person's access to a disk and logs it; returns the previous access."""
    row = (
        await db.execute(
            select(DiskAccess.access).where(
                DiskAccess.volume_id == volume.id, DiskAccess.membership_id == membership_id
            )
        )
    ).scalar_one_or_none()
    previous = _level(row)
    if previous == access:
        return previous
    await db.execute(
        insert(DiskAccess)
        .values(
            organization_id=organization_id,
            volume_id=volume.id,
            membership_id=membership_id,
            access=access,
            set_by_user_id=actor.user_id,
        )
        .on_conflict_do_update(
            constraint="uq_disk_access_volume_id_membership_id",
            set_={"access": access, "set_by_user_id": actor.user_id, "updated_at": utcnow()},
        )
    )
    record(
        db,
        organization_id,
        "disk_access_changed",
        actor,
        Target("disk", volume.id, volume.name),
        {"person": person_label, "from": previous, "to": access},
    )
    return previous


async def _owed_pairs(db: AsyncSession, organization_id: UUID) -> set[Pair]:
    """(volume, membership) pairs with something still owed to an instance or the provider."""
    users = (
        await db.execute(
            select(MetaUser.volume_id, Device.membership_id)
            .join(
                Device,
                (Device.id == MetaUser.device_id)
                & (Device.organization_id == MetaUser.organization_id),
            )
            .where(MetaUser.organization_id == organization_id, MetaUser.applied_at.is_(None))
        )
    ).all()
    grants = (
        await db.execute(
            select(S3KeyGrant.volume_id, Device.membership_id)
            .join(
                S3Key,
                (S3Key.id == S3KeyGrant.key_id)
                & (S3Key.organization_id == S3KeyGrant.organization_id),
            )
            .join(
                Device,
                (Device.id == S3Key.device_id) & (Device.organization_id == S3Key.organization_id),
            )
            .where(S3KeyGrant.organization_id == organization_id, S3KeyGrant.applied_at.is_(None))
        )
    ).all()
    return {(v, m) for v, m in [*users, *grants]}


async def list_access(ctx: Context, organization_id: UUID) -> list[DiskAccessOut]:
    """Every (live disk, live person) pair with its access, `none` included."""
    async with ctx.sessions() as db:
        rows = (
            await db.execute(
                select(Volume.id, Membership.id, Membership.user_id, DiskAccess.access)
                .join(Membership, Membership.organization_id == Volume.organization_id)
                .outerjoin(
                    DiskAccess,
                    (DiskAccess.volume_id == Volume.id)
                    & (DiskAccess.membership_id == Membership.id),
                )
                .where(
                    Volume.organization_id == organization_id,
                    Volume.deleted_at.is_(None),
                    Membership.status != "removed",
                    Membership.deleted_at.is_(None),
                )
                .order_by(Volume.created_at, Membership.created_at)
            )
        ).all()
        owed = await _owed_pairs(db, organization_id)
    return [
        DiskAccessOut(
            disk_id=volume_id,
            person_id=user_id,
            access=access or "none",
            applied=(volume_id, membership_id) not in owed,
        )
        for volume_id, membership_id, user_id, access in rows
    ]


async def my_disks(ctx: Context, actor: Actor) -> list[MyDiskOut]:
    """The ready disks the person may read or write, in creation order."""
    async with ctx.sessions() as db:
        rows = (
            await db.execute(
                select(Volume.id, Volume.name, DiskAccess.access)
                .join(
                    DiskAccess,
                    (DiskAccess.volume_id == Volume.id)
                    & (DiskAccess.organization_id == Volume.organization_id),
                )
                .where(
                    Volume.organization_id == actor.organization_id,
                    DiskAccess.membership_id == actor.membership_id,
                    DiskAccess.access.in_(("read", "write")),
                    Volume.state == "ready",
                    Volume.deleted_at.is_(None),
                )
                .order_by(Volume.created_at)
            )
        ).all()
    return [MyDiskOut(id=v, name=name, access=access) for v, name, access in rows]


async def set_access(
    ctx: Context, actor: Actor, volume_id: UUID, user_id: UUID, access: AccessLevel
) -> DiskAccessOut:
    """
    Sets a person's access to a disk and applies it live to their devices: the ACL on the
    disk's instance, the grant at the provider (managed). In manual mode, lowering to `none`
    someone whose device could see the disk opens that disk's key window.
    """
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, actor.organization_id)
        org = await db.get(Organization, actor.organization_id)
        volume = (
            await db.execute(
                select(Volume).where(
                    Volume.organization_id == actor.organization_id,
                    Volume.id == volume_id,
                    Volume.deleted_at.is_(None),
                    Volume.state != "failed",
                )
            )
        ).scalar_one_or_none()
        if org is None or volume is None:
            raise not_found("No such disk")
        row = (
            await db.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.organization_id == actor.organization_id,
                    Membership.user_id == user_id,
                    Membership.status != "removed",
                    Membership.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise not_found("No such person")
        membership, user = row
        label = user.name or user.email
        device_ids = list(
            (
                await db.execute(
                    select(Device.id).where(
                        Device.organization_id == org.id,
                        Device.membership_id == membership.id,
                        Device.state == "active",
                    )
                )
            ).scalars()
        )
        # Read before the change: a device that had a user on the instance could read the key.
        if (
            access == "none"
            and org.storage_mode == "manual"
            and volume.id in await could_see(db, org.id, device_ids)
        ):
            open_exposure(
                db, org.id, volume.id, kind="access_removed", label=label,
                membership_id=membership.id,
            )  # fmt: skip
        await put_access(
            db,
            org.id,
            volume,
            membership.id,
            access,
            actor=AuditActor.person(actor.user_id, actor.label),
            person_label=label,
        )
        await sync(db, org.id, device_ids=device_ids, volume_ids=[volume.id])
    applied = await apply_owed(ctx, actor.organization_id, device_ids=device_ids)
    return DiskAccessOut(disk_id=volume_id, person_id=user_id, access=access, applied=applied)


async def membership_of(db: AsyncSession, organization_id: UUID, user_id: UUID) -> Membership:
    """A person's live membership, or 404."""
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.organization_id == organization_id,
                Membership.user_id == user_id,
                Membership.status != "removed",
                Membership.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise ApiError(422, "validation", "No such person", {"field": "personId"})
    return membership
