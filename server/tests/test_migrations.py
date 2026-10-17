"""
The migrations on a database of their own: every revision upgrades and downgrades cleanly,
`0003` matches the models, and `0003` refuses a database that holds an organization of
`0002` in either direction (docs/DATABASE_SCHEMA.md, "Migration 0003").
"""

import asyncio
import os
from collections.abc import Iterator
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from floppy_server.db import Base
from tests.conftest import SERVER_ROOT, TEST_DB, owner_url

SCRATCH = f"{TEST_DB}_migrations"


async def _sql(statement: str) -> None:
    dsn = owner_url("floppy").replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(statement)
    finally:
        await conn.close()


@pytest.fixture
def scratch() -> Iterator[Config]:
    """An empty database and an Alembic configuration pointing at it; dropped afterwards."""
    asyncio.run(_sql(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
    asyncio.run(_sql(f"CREATE DATABASE {SCRATCH}"))
    config = Config(os.path.join(SERVER_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(SERVER_ROOT, "migrations"))
    config.attributes["database_url"] = owner_url(SCRATCH)
    config.attributes["configure_logger"] = False
    yield config
    asyncio.run(_sql(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))


def _run(statements: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(owner_url(SCRATCH))
        try:
            out: list[Any] = []
            async with engine.begin() as conn:
                for statement, params in statements:
                    result = await conn.execute(text(statement), params)
                    out.append(result.all() if result.returns_rows else None)
            return out
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _diff() -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(owner_url(SCRATCH))
        try:
            async with engine.connect() as conn:

                def compare(sync: Connection) -> list[Any]:
                    return list(compare_metadata(MigrationContext.configure(sync), Base.metadata))

                return await conn.run_sync(compare)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_every_revision_upgrades_and_downgrades(scratch: Config) -> None:
    command.upgrade(scratch, "head")
    assert _diff() == []
    command.downgrade(scratch, "0002")
    command.upgrade(scratch, "head")
    command.downgrade(scratch, "base")
    command.upgrade(scratch, "head")
    assert _diff() == []


def test_0003_refuses_an_organization_of_0002_both_ways(scratch: Config) -> None:
    command.upgrade(scratch, "0002")
    insert = (
        "INSERT INTO organizations (name, slug, s3_endpoint, s3_region, s3_bucket, valkey_url)"
        " VALUES ('Studio', 'studio', 'https://s3.test', '', 'b', 'redis://v:6379')"
    )
    _run([(insert, {})])
    with pytest.raises(RuntimeError, match="holds 1 organization"):
        command.upgrade(scratch, "head")
    _run([("UPDATE organizations SET deleted_at = now()", {})])  # still a row: still refused
    with pytest.raises(RuntimeError, match="recreate the database"):
        command.upgrade(scratch, "head")
    assert _run([("SELECT version_num FROM alembic_version", {})]) == [[("0002",)]]


def test_0003_downgrade_refuses_organizations_too(scratch: Config) -> None:
    command.upgrade(scratch, "head")
    _run(
        [
            (
                "INSERT INTO organizations (name, slug, s3_endpoint, s3_region)"
                " VALUES ('Studio', 'studio', 'https://s3.test', '')",
                {},
            )
        ]
    )
    with pytest.raises(RuntimeError, match="0003 downgrade"):
        command.downgrade(scratch, "0002")
