"""
Per-person and per-Mac S3 keys (DATABASE_SCHEMA.md, "Storage modes"; DECISIONS #22).

With `person` or `device` scope the volumes' formats hold no key: each Mac mounts with its own
key, its person's or its own, and the control plane formats and probes with a key of its own
(`holder = 'control_plane'`). When access ends the reached key goes `retiring`: in a manual
organization the admin deletes it at the provider and confirms, in a managed one the adapter
deletes it right after the commit, and the reconciler retries when the provider does not
answer. There is no key window with these scopes.
"""

import logging
from uuid import UUID, uuid7

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, invalid_state, not_found
from floppy_server.infra.juicefs import S3Credentials
from floppy_server.infra.providers import ProviderError
from floppy_server.models import Device, Membership, Organization, S3Key, User

log = logging.getLogger(__name__)


def provider_failed(error: ProviderError) -> ApiError:
    """502: the storage provider refused or did not answer."""
    return ApiError(
        502, "provider_failed", "The storage provider did not do it", {"reason": str(error)[:200]}
    )


def probe_failed(code: str | None, request: str | None) -> ApiError:
    """422: the test write with the key failed."""
    return ApiError(
        422, "probe_failed", "The test write failed", {"code": code or "", "request": request or ""}
    )


async def probe(ctx: Context, org: Organization, key: S3Credentials, label: str) -> None:
    """A test write on the organization's bucket with a key; raises `probe_failed`."""
    result = await ctx.prober.probe(
        endpoint=org.s3_endpoint, region=org.s3_region, bucket=org.s3_bucket, key=key, label=label
    )
    if not result.ok:
        raise probe_failed(result.code, result.request)


async def person_key(db: AsyncSession, organization_id: UUID, membership_id: UUID) -> S3Key | None:
    """A person's live key."""
    return (
        await db.execute(
            select(S3Key).where(
                S3Key.organization_id == organization_id,
                S3Key.holder == "person",
                S3Key.membership_id == membership_id,
                S3Key.state == "active",
            )
        )
    ).scalar_one_or_none()


async def add_person_key(
    ctx: Context,
    db: AsyncSession,
    org: Organization,
    membership: Membership,
    label: str,
    actor: AuditActor,
    pasted: S3Credentials | None,
) -> S3Key:
    """
    A person's new active key, pasted (manual, already probed by the caller) or created by
    the adapter (managed); their previous live key goes `retiring`.
    """
    now = utcnow()
    await db.execute(
        update(S3Key)
        .where(
            S3Key.organization_id == org.id,
            S3Key.holder == "person",
            S3Key.membership_id == membership.id,
            S3Key.state.in_(("pending", "active")),
        )
        .values(state="retiring", retiring_at=now)
    )
    key = S3Key(id=uuid7(), organization_id=org.id, holder="person", membership_id=membership.id)
    if pasted is not None:
        key.access_key_id, secret = pasted.access_key_id, pasted.secret_key
        key.created_by_user_id = actor.user_id
    else:
        try:
            created = await ctx.adapter_for(org).create_key(
                org.s3_bucket, f"floppy-person-{membership.id}"
            )
        except ProviderError as error:
            raise provider_failed(error) from error
        key.access_key_id, secret = created.access_key_id, created.secret_key
        key.origin, key.provider_ref = "provider", created.provider_ref
    key.secret_key_enc = ctx.box.seal(
        secret, table="s3_keys", column="secret_key_enc", row_id=key.id
    )
    key.state, key.activated_at = "active", now
    db.add(key)
    await db.flush()
    await db.refresh(key)
    record(
        db,
        org.id,
        "key_created",
        actor,
        Target("key", key.id, key.access_key_tail),
        {"holder": "person", "accessKeyTail": key.access_key_tail, "for": label},
    )
    return key


async def add_device_key(
    ctx: Context, db: AsyncSession, org: Organization, device: Device, actor: AuditActor
) -> S3Credentials:
    """
    A Mac's own key, created by the adapter; the secret is returned, never stored. A previous
    live key of the same Mac (a reissue) goes `retiring`.
    """
    now = utcnow()
    await db.execute(
        update(S3Key)
        .where(
            S3Key.organization_id == org.id,
            S3Key.holder == "device",
            S3Key.device_id == device.id,
            S3Key.state.in_(("pending", "active")),
        )
        .values(state="retiring", retiring_at=now)
    )
    try:
        created = await ctx.adapter_for(org).create_key(org.s3_bucket, f"floppy-mac-{device.id}")
    except ProviderError as error:
        raise provider_failed(error) from error
    key = S3Key(
        id=uuid7(),
        organization_id=org.id,
        holder="device",
        device_id=device.id,
        access_key_id=created.access_key_id,
        origin="provider",
        provider_ref=created.provider_ref,
        state="active",
        activated_at=now,
    )
    db.add(key)
    await db.flush()
    await db.refresh(key)
    record(
        db,
        org.id,
        "key_created",
        actor,
        Target("key", key.id, key.access_key_tail),
        {"holder": "device", "accessKeyTail": key.access_key_tail, "for": device.name},
    )
    return S3Credentials(created.access_key_id, created.secret_key)


async def retire_keys(
    db: AsyncSession,
    organization_id: UUID,
    *,
    membership_id: UUID | None = None,
    device_id: UUID | None = None,
) -> int:
    """Puts a person's or a Mac's live keys in `retiring`: their deletion is owed."""
    query = update(S3Key).where(
        S3Key.organization_id == organization_id, S3Key.state.in_(("pending", "active"))
    )
    if membership_id is not None:
        query = query.where(S3Key.holder == "person", S3Key.membership_id == membership_id)
    elif device_id is not None:
        query = query.where(S3Key.holder == "device", S3Key.device_id == device_id)
    else:
        raise ValueError("a person or a Mac")
    result = await db.execute(
        query.values(state="retiring", retiring_at=utcnow()).returning(S3Key.id)
    )
    return len(result.all())


async def delete_owed_keys(ctx: Context, organization_id: UUID) -> int:
    """
    Managed organizations: deletes at the provider every `retiring` key it created, then marks
    it `retired`. Keys the provider does not delete now stay owed for the next pass.
    """
    async with ctx.sessions() as db:
        org = await db.get(Organization, organization_id)
        if org is None or org.storage_mode != "managed":
            return 0
        owed = list(
            (
                await db.execute(
                    select(S3Key).where(
                        S3Key.organization_id == organization_id,
                        S3Key.origin == "provider",
                        S3Key.state == "retiring",
                    )
                )
            ).scalars()
        )
    if not owed:
        return 0
    try:
        adapter = ctx.adapter_for(org)
    except ProviderError as error:
        log.warning("keys owed to the provider stay owed: %s", error)
        return 0
    deleted = 0
    for key in owed:
        if key.provider_ref is None:
            continue
        try:
            await adapter.delete_key(key.provider_ref)
        except ProviderError as error:
            log.warning("deleting a key at the provider failed, retried later: %s", error)
            continue
        async with ctx.sessions() as db, db.begin():
            done = await db.execute(
                update(S3Key)
                .where(S3Key.id == key.id, S3Key.state == "retiring")
                .values(state="retired", retired_at=utcnow(), secret_key_enc=None)
                .returning(S3Key.id)
            )
            if done.first() is not None:
                record(
                    db,
                    organization_id,
                    "key_deleted",
                    AuditActor.system(),
                    Target("key", key.id, key.access_key_tail),
                    {"holder": key.holder, "accessKeyTail": key.access_key_tail, "by": "adapter"},
                )
                deleted += 1
    return deleted


async def confirm_deleted(
    ctx: Context, organization_id: UUID, key_id: UUID, actor: AuditActor
) -> None:
    """Manual: the admin confirms a `retiring` person key is deleted at the provider."""
    async with ctx.sessions() as db, db.begin():
        org = await db.get(Organization, organization_id)
        key = (
            await db.execute(
                select(S3Key)
                .where(S3Key.organization_id == organization_id, S3Key.id == key_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if org is None or key is None:
            raise not_found("No such key")
        if org.storage_mode != "manual" or key.holder not in ("person", "device"):
            raise invalid_state("Only a person's key in a manual organization is confirmed by hand")
        if key.state != "retiring":
            raise invalid_state("Only a key whose deletion is owed can be confirmed")
        key.state, key.retired_at, key.secret_key_enc = "retired", utcnow(), None
        record(
            db,
            organization_id,
            "key_deleted",
            actor,
            Target("key", key.id, key.access_key_tail),
            {"holder": key.holder, "accessKeyTail": key.access_key_tail, "by": "admin"},
        )


async def owner_label(db: AsyncSession, membership_id: UUID) -> str:
    """A membership's person, as the audit log names them."""
    row = (
        await db.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.id == membership_id)
        )
    ).scalar_one_or_none()
    return (row.name or row.email) if row else ""
