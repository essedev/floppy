"""
Keys made by the storage adapter in managed mode (DATABASE_SCHEMA.md, "Storage modes";
DECISIONS #24): one per device, granted per bucket (`access.service` keeps the grants), and the
control plane's own key, owner of every bucket it creates.

The formats hold no key: each mount brings its device's own. When a device is revoked or gets
a new key, the old one goes `retiring` in the same transaction and the adapter deletes it at
the provider right after the commit; the reconciler retries when the provider does not answer.
There is no key window in managed mode.
"""

import logging
from uuid import UUID, uuid7

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError
from floppy_server.infra.juicefs import S3Credentials
from floppy_server.infra.providers import ProviderError
from floppy_server.models import Device, Organization, S3Key

log = logging.getLogger(__name__)


def provider_failed(error: ProviderError) -> ApiError:
    """502: the storage provider refused or did not answer."""
    return ApiError(
        502, "provider_failed", "The storage provider did not do it", {"reason": str(error)[:200]}
    )


async def control_plane_key(db: AsyncSession, organization_id: UUID) -> S3Key | None:
    """The control plane's own active key (managed): it formats and probes every bucket."""
    return (
        await db.execute(
            select(S3Key).where(
                S3Key.organization_id == organization_id,
                S3Key.holder == "control_plane",
                S3Key.state == "active",
            )
        )
    ).scalar_one_or_none()


async def device_key(db: AsyncSession, organization_id: UUID, device_id: UUID) -> S3Key | None:
    """A device's live key."""
    return (
        await db.execute(
            select(S3Key).where(
                S3Key.organization_id == organization_id,
                S3Key.holder == "device",
                S3Key.device_id == device_id,
                S3Key.state == "active",
            )
        )
    ).scalar_one_or_none()


async def add_device_key(
    ctx: Context, db: AsyncSession, org: Organization, device: Device, actor: AuditActor
) -> S3Credentials:
    """
    A device's own key, created by the adapter with no grant; the secret is returned, never
    stored. A previous live key of the same device (a reissue) goes `retiring`. The grants
    follow from `access.service.sync`.
    """
    await retire_device_keys(db, org.id, device.id)
    try:
        created = await ctx.adapter_for(org).create_key(f"floppy-device-{device.id}")
    except ProviderError as error:
        raise provider_failed(error) from error
    now = utcnow()
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


async def retire_device_keys(db: AsyncSession, organization_id: UUID, device_id: UUID) -> int:
    """Puts a device's live keys in `retiring`: their deletion at the provider is owed."""
    result = await db.execute(
        update(S3Key)
        .where(
            S3Key.organization_id == organization_id,
            S3Key.holder == "device",
            S3Key.device_id == device_id,
            S3Key.state.in_(("pending", "active")),
        )
        .values(state="retiring", retiring_at=utcnow())
        .returning(S3Key.id)
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
