"""The schema's own guarantees: never DELETE, the append-only audit log, tenant-safe references."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.db import Base
from tests.conftest import Org, make_org

TABLES = [t.name for t in Base.metadata.sorted_tables]


async def test_every_table_is_migrated(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        present = set(
            (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            ).scalars()
        )
    assert set(TABLES) <= present
    assert len(TABLES) == 16


@pytest.mark.parametrize("table", TABLES)
async def test_the_application_role_cannot_delete_or_truncate(
    table: str, ctx: Context, app_engine: AsyncEngine
) -> None:
    for statement in (f"DELETE FROM {table}", f"TRUNCATE {table}"):
        async with app_engine.connect() as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(text(statement))


async def test_the_application_role_cannot_rewrite_the_audit_log(
    org: Org, ctx: Context, admin: object, app_engine: AsyncEngine
) -> None:
    async with app_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(text("UPDATE audit_events SET target_label = 'x'"))


async def test_the_trigger_refuses_even_a_role_granted_update(
    org: Org, ctx: Context, admin: object, owner_engine: AsyncEngine
) -> None:
    role = f"floppy_test_{uuid.uuid4().hex[:8]}"
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"CREATE ROLE {role}"))
        await conn.execute(text(f"GRANT SELECT, UPDATE, DELETE ON audit_events TO {role}"))
    try:
        for statement in ("UPDATE audit_events SET target_label = 'x'", "DELETE FROM audit_events"):
            async with owner_engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL ROLE {role}"))
                with pytest.raises(DBAPIError, match="append only"):
                    await conn.execute(text(statement))
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text(f"REVOKE ALL ON audit_events FROM {role}"))
            await conn.execute(text(f"DROP ROLE {role}"))


async def test_a_row_cannot_point_at_another_organizations_row(
    ctx: Context, app_engine: AsyncEngine
) -> None:
    a = await make_org(ctx, "studio-a", "a@a.test")
    b = await make_org(ctx, "studio-b", "b@b.test", key="AKIASTUDIOBKEY001")
    async with app_engine.connect() as conn:
        membership_b = (
            await conn.execute(
                text("SELECT id FROM memberships WHERE organization_id = :b"), {"b": b.id}
            )
        ).scalar_one()
        user_b = (
            await conn.execute(
                text("SELECT user_id FROM memberships WHERE id = :m"), {"m": membership_b}
            )
        ).scalar_one()
        # An invitation of organization A for a membership of organization B.
        with pytest.raises(IntegrityError, match="fk_invitations_organization_id_memberships"):
            await conn.execute(
                text(
                    "INSERT INTO invitations (organization_id, membership_id, token_hash, sent_by_user_id,"
                    " expires_at) VALUES (:a, :m, decode(repeat('ab', 32), 'hex'), :u, now() + interval '1 day')"
                ),
                {"a": a.id, "m": membership_b, "u": user_b},
            )


async def test_ids_are_uuidv7_and_timestamps_utc(org: Org, owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        version, zone = (
            await conn.execute(
                text(
                    "SELECT uuid_extract_version(id), extract(timezone FROM created_at) FROM organizations"
                )
            )
        ).one()
    assert (version, zone) == (7, 0)


async def test_constraints_hold_the_state_machines(
    org: Org, ctx: Context, owner_engine: AsyncEngine
) -> None:
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_memberships_removed_at"):
            await conn.execute(text("UPDATE memberships SET status = 'removed'"))
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="ck_s3_keys_secret_wiped"):
            await conn.execute(text("UPDATE s3_keys SET state = 'retired', retired_at = now()"))
    async with owner_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="uq_s3_keys_organization_id_active"):
            await conn.execute(
                text(
                    "INSERT INTO s3_keys (organization_id, access_key_id, state, secret_key_enc)"
                    " SELECT organization_id, 'AKIAANOTHER00009', 'active', secret_key_enc FROM s3_keys"
                )
            )
