"""
People: invitations, resends, role changes and removal (admins only).

A person in the API is a user plus their latest membership in the organization; a removed
person stays listed. Organization-level invariants (the last admin) are checked under the
organization's row lock. Removing a person revokes their sessions, invitations and Macs, and
opens the key window when one of their Macs held credentials (DECISIONS #12).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, true, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from floppy_server import mailtext
from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.auth.service import INVITATION_TTL, mail_language, send_mail
from floppy_server.context import Context, utcnow
from floppy_server.crypto import new_token, token_hash
from floppy_server.devices.service import delete_valkey_users, retire_reached_keys, revoke_rows
from floppy_server.errors import ApiError, invalid_state, not_found, validation
from floppy_server.infra.juicefs import S3Credentials
from floppy_server.keys import scoped
from floppy_server.keys.service import open_exposure
from floppy_server.models import (
    Device,
    Invitation,
    Membership,
    Organization,
    User,
    WebSession,
    lock_organization,
)

Role = Literal["admin", "member"]


class PersonOut(CamelModel):
    """A person in the organization: a user and their membership."""

    id: UUID
    name: str | None
    email: str
    role: Role
    status: Literal["invited", "active", "removed"]
    device_count: int
    last_sign_in_at: datetime | None
    invited_at: datetime | None
    invited_by: str | None
    invite_expires_at: datetime | None
    removed_at: datetime | None
    is_me: bool


def already_member() -> ApiError:
    """409: the address already has a membership that is not removed."""
    return ApiError(409, "already_member", "Already in the organization")


def last_admin() -> ApiError:
    """409: the organization must keep one active admin."""
    return ApiError(409, "last_admin", "The organization needs an admin")


async def _people(
    db: AsyncSession, organization_id: UUID, me: UUID, user_id: UUID | None = None
) -> list[PersonOut]:
    """Each user's latest membership in the organization, with counts and invitation dates."""
    latest = (
        select(Membership)
        .where(Membership.organization_id == organization_id, Membership.deleted_at.is_(None))
        .ext(distinct_on(Membership.user_id))
        .order_by(Membership.user_id, Membership.created_at.desc())
    )
    if user_id is not None:
        latest = latest.where(Membership.user_id == user_id)
    membership = aliased(Membership, latest.subquery())
    inviter = aliased(User)
    last_invitation = (
        select(Invitation.sent_at, Invitation.expires_at, Invitation.sent_by_user_id)
        .where(
            Invitation.organization_id == organization_id, Invitation.membership_id == membership.id
        )
        .order_by(Invitation.sent_at.desc())
        .limit(1)
        .lateral()
    )
    device_count = (
        select(func.count())
        .where(
            Device.organization_id == organization_id,
            Device.membership_id == membership.id,
            Device.state != "revoked",
        )
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                User,
                membership,
                last_invitation.c.sent_at,
                last_invitation.c.expires_at,
                inviter,
                device_count,
            )
            .join(membership, membership.user_id == User.id)
            .outerjoin(last_invitation, true())
            .outerjoin(inviter, inviter.id == last_invitation.c.sent_by_user_id)
        )
    ).all()
    people = [
        PersonOut(
            id=user.id,
            name=user.name,
            email=user.email,
            role=m.role,
            status=m.status,
            device_count=macs,
            last_sign_in_at=user.last_sign_in_at,
            invited_at=sent_at,
            invited_by=(inv.name or inv.email) if inv is not None else None,
            invite_expires_at=expires_at if m.status == "invited" else None,
            removed_at=m.removed_at,
            is_me=user.id == me,
        )
        for user, m, sent_at, expires_at, inv, macs in rows
    ]
    order = {"active": 0, "invited": 1, "removed": 2}
    return sorted(people, key=lambda p: (order[p.status], (p.name or p.email).lower()))


async def list_people(ctx: Context, actor: Actor) -> list[PersonOut]:
    """Everyone in the organization, removed people included."""
    async with ctx.sessions() as db:
        return await _people(db, actor.organization_id, actor.user_id)


async def get_person(ctx: Context, actor: Actor, user_id: UUID) -> PersonOut:
    """One person of the organization."""
    async with ctx.sessions() as db:
        people = await _people(db, actor.organization_id, actor.user_id, user_id)
    if not people:
        raise not_found("No such person")
    return people[0]


async def _live_membership(
    db: AsyncSession, organization_id: UUID, user_id: UUID
) -> Membership | None:
    return (
        await db.execute(
            select(Membership)
            .where(
                Membership.organization_id == organization_id,
                Membership.user_id == user_id,
                Membership.status != "removed",
                Membership.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _active_admins(db: AsyncSession, organization_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).where(
                Membership.organization_id == organization_id,
                Membership.role == "admin",
                Membership.status == "active",
                Membership.deleted_at.is_(None),
            )
        )
        or 0
    )


async def _invite_mail(
    ctx: Context,
    db: AsyncSession,
    org: Organization,
    membership: Membership,
    user: User,
    actor: Actor,
    now: datetime,
) -> None:
    """A new invitation row and its mail (the previous live one superseded)."""
    await db.execute(
        update(Invitation)
        .where(
            Invitation.organization_id == org.id,
            Invitation.membership_id == membership.id,
            Invitation.state == "sent",
        )
        .values(state="superseded", superseded_at=now)
    )
    token = new_token()
    db.add(
        Invitation(
            organization_id=org.id,
            membership_id=membership.id,
            token_hash=token_hash(token),
            sent_by_user_id=actor.user_id,
            sent_at=now,
            expires_at=now + INVITATION_TTL,
        )
    )
    membership.invited_at = now
    await db.flush()
    mail = mailtext.invitation(
        lang=mail_language(org),
        to=user.email,
        organization=org.name,
        inviter=actor.label,
        public_url=ctx.settings.public_url,
        token=token,
    )
    await send_mail(ctx, db, mail, org)


async def _check_key_for(ctx: Context, org: Organization, key: S3Credentials | None) -> None:
    """A pasted person key is wanted exactly in manual `person` scope, and probed first."""
    pasted = org.storage_mode == "manual" and org.storage_key_scope == "person"
    if pasted and key is None:
        raise validation("This organization gives each person a key: paste it", {"field": "key"})
    if not pasted and key is not None:
        raise validation("This organization takes no pasted person keys", {"field": "key"})
    if key is not None:
        await scoped.probe(ctx, org, key, "person")


async def invite(
    ctx: Context, actor: Actor, email: str, role: Role, key: S3Credentials | None = None
) -> PersonOut:
    """
    Invites an address: a user if new, an `invited` membership, an invitation and its mail.
    With `person` keys, the person's key too: pasted and probed (manual) or created (managed).
    """
    address = email.strip().lower()
    now = utcnow()
    async with ctx.sessions() as db:
        org_now = await db.get(Organization, actor.organization_id)
    if org_now is None:
        raise not_found()
    await _check_key_for(ctx, org_now, key)
    async with ctx.sessions() as db, db.begin():
        org = await db.get(Organization, actor.organization_id)
        if org is None:
            raise not_found()
        user = (
            await db.execute(
                select(User)
                .where(User.email == address, User.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(email=address)
            db.add(user)
            await db.flush()
        else:
            # One organization per address until the hosted service has an organization
            # picker (DATABASE_SCHEMA.md, open question 1).
            elsewhere = await db.scalar(
                select(func.count()).where(
                    Membership.user_id == user.id,
                    Membership.status != "removed",
                    Membership.deleted_at.is_(None),
                )
            )
            if elsewhere:
                raise already_member()
        membership = Membership(
            organization_id=org.id,
            user_id=user.id,
            role=role,
            status="invited",
            invited_by_user_id=actor.user_id,
            invited_at=now,
        )
        db.add(membership)
        await db.flush()
        if org.storage_key_scope == "person":
            await scoped.add_person_key(
                ctx,
                db,
                org,
                membership,
                address,
                AuditActor.person(actor.user_id, actor.label),
                key,
            )
        await _invite_mail(ctx, db, org, membership, user, actor, now)
        record(
            db,
            org.id,
            "person_invited",
            AuditActor.person(actor.user_id, actor.label),
            Target("person", user.id, address),
            {"role": role},
        )
        user_id = user.id
    return await get_person(ctx, actor, user_id)


async def replace_key(ctx: Context, actor: Actor, user_id: UUID, key: S3Credentials) -> None:
    """
    Manual `person` scope: the admin pastes a person's new key, probed first; the old one goes
    `retiring` until the admin confirms its deletion at the provider.
    """
    async with ctx.sessions() as db:
        org = await db.get(Organization, actor.organization_id)
    if org is None:
        raise not_found()
    if org.storage_mode != "manual" or org.storage_key_scope != "person":
        raise invalid_state("Only a manual organization with person keys takes a pasted key")
    await scoped.probe(ctx, org, key, "person")
    async with ctx.sessions() as db, db.begin():
        org_row = await db.get(Organization, actor.organization_id)
        membership = await _live_membership(db, actor.organization_id, user_id)
        user = await db.get(User, user_id)
        if org_row is None or membership is None or user is None:
            raise not_found("No such person")
        await scoped.add_person_key(
            ctx,
            db,
            org_row,
            membership,
            user.name or user.email,
            AuditActor.person(actor.user_id, actor.label),
            key,
        )


async def resend(ctx: Context, actor: Actor, user_id: UUID) -> PersonOut:
    """A new invitation link for someone still invited; the previous link stops working."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        org = await db.get(Organization, actor.organization_id)
        membership = await _live_membership(db, actor.organization_id, user_id)
        user = await db.get(User, user_id)
        if org is None or membership is None or user is None:
            raise not_found("No such person")
        if membership.status != "invited":
            raise invalid_state("Only an invited person can get a new invitation")
        await _invite_mail(ctx, db, org, membership, user, actor, now)
        record(
            db,
            org.id,
            "invite_resent",
            AuditActor.person(actor.user_id, actor.label),
            Target("person", user.id, user.name or user.email),
            {"role": membership.role},
        )
    return await get_person(ctx, actor, user_id)


async def change_role(ctx: Context, actor: Actor, user_id: UUID, role: Role) -> PersonOut:
    """Changes a person's role; demoting the last active admin fails."""
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, actor.organization_id)
        membership = await _live_membership(db, actor.organization_id, user_id)
        user = await db.get(User, user_id)
        if membership is None or user is None:
            raise not_found("No such person")
        if membership.role != role:
            if (
                membership.role == "admin"
                and membership.status == "active"
                and await _active_admins(db, actor.organization_id) <= 1
            ):
                raise last_admin()
            previous = membership.role
            membership.role = role
            record(
                db,
                actor.organization_id,
                "role_changed",
                AuditActor.person(actor.user_id, actor.label),
                Target("person", user.id, user.name or user.email),
                {"from": previous, "to": role},
            )
    return await get_person(ctx, actor, user_id)


async def remove(ctx: Context, actor: Actor, user_id: UUID) -> tuple[PersonOut, int]:
    """
    Removes a person: membership `removed`, invitations revoked, sessions ended, Macs revoked
    (`member_removed`), and one key exposure if a Mac had credentials. Returns the person and
    how many Macs were revoked.
    """
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        await lock_organization(db, actor.organization_id)
        if user_id == actor.user_id:
            raise ApiError(409, "cannot_remove_self", "Cannot remove yourself")
        membership = await _live_membership(db, actor.organization_id, user_id)
        user = await db.get(User, user_id)
        if membership is None or user is None:
            raise not_found("No such person")
        if (
            membership.role == "admin"
            and membership.status == "active"
            and await _active_admins(db, actor.organization_id) <= 1
        ):
            raise last_admin()
        label = user.name or user.email
        who = AuditActor.person(actor.user_id, actor.label)
        membership.status = "removed"
        membership.removed_at = now
        membership.removed_by_user_id = actor.user_id
        await db.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == actor.organization_id,
                Invitation.membership_id == membership.id,
                Invitation.state == "sent",
            )
            .values(state="revoked", revoked_at=now)
        )
        await db.execute(
            update(WebSession)
            .where(
                WebSession.organization_id == actor.organization_id,
                WebSession.membership_id == membership.id,
                WebSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoke_reason="membership_removed")
        )
        devices = list(
            (
                await db.execute(
                    select(Device)
                    .where(
                        Device.organization_id == actor.organization_id,
                        Device.membership_id == membership.id,
                        Device.state != "revoked",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        exposed = revoke_rows(db, devices, reason="member_removed", actor=who, now=now)
        org = await db.get(Organization, actor.organization_id)
        if org is None:
            raise not_found()
        await retire_reached_keys(ctx, db, org, devices, "member_removed")
        if org.storage_key_scope == "person":
            await scoped.retire_keys(db, org.id, membership_id=membership.id)
        if exposed and org.storage_key_scope == "team":
            open_exposure(
                db,
                actor.organization_id,
                kind="person_removed",
                label=label,
                membership_id=membership.id,
            )
        record(
            db,
            actor.organization_id,
            "person_removed",
            who,
            Target("person", user.id, label),
            {"devices": len(devices)},
        )
        device_ids = [d.id for d in devices]
    await delete_valkey_users(ctx, actor.organization_id, device_ids)
    await scoped.delete_owed_keys(ctx, actor.organization_id)
    return await get_person(ctx, actor, user_id), len(device_ids)
