"""
Re-sealing stored secrets after a new key joins the ring (DATABASE_SCHEMA.md, "Secrets"):
new writes already use the newest key; this moves the old rows to it, after which the old
key can leave `FLOPPY_SECRET_KEYS`.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.context import Context
from floppy_server.crypto import SecretBox
from floppy_server.models import Organization, S3Key

ORGANIZATION_COLUMNS = ("valkey_admin_password_enc", "smtp_password_enc")


def _reseal(
    box: SecretBox, blob: bytes | None, table: str, column: str, row_id: UUID
) -> bytes | None:
    """The blob sealed with the current key, or None when it already is (or is empty)."""
    if blob is None or box.key_id_of(blob) == box.current_key_id:
        return None
    plain = box.open(blob, table=table, column=column, row_id=row_id)
    return box.seal(plain, table=table, column=column, row_id=row_id)


async def _organizations(db: AsyncSession, box: SecretBox) -> int:
    moved = 0
    for org in (await db.execute(select(Organization).with_for_update())).scalars():
        for column in ORGANIZATION_COLUMNS:
            sealed = _reseal(box, getattr(org, column), "organizations", column, org.id)
            if sealed is not None:
                setattr(org, column, sealed)
                moved += 1
    return moved


async def _keys(db: AsyncSession, box: SecretBox) -> int:
    moved = 0
    for key in (
        await db.execute(select(S3Key).where(S3Key.secret_key_enc.is_not(None)).with_for_update())
    ).scalars():
        sealed = _reseal(box, key.secret_key_enc, "s3_keys", "secret_key_enc", key.id)
        if sealed is not None:
            key.secret_key_enc = sealed
            moved += 1
    return moved


async def reencrypt_all(ctx: Context) -> int:
    """Re-seals every stored secret not sealed with the current key; returns how many moved."""
    async with ctx.sessions() as db, db.begin():
        return await _organizations(db, ctx.box) + await _keys(db, ctx.box)
