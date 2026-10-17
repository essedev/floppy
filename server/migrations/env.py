"""
Alembic environment: migrations run as the database owner, with the URL from
`FLOPPY_MIGRATION_DATABASE_URL` (or `FLOPPY_DATABASE_URL`), never from alembic.ini, so no
credentials sit in a tracked file. `floppy-server migrate` passes the URL directly.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from floppy_server import models  # noqa: F401  (registers the tables on Base.metadata)
from floppy_server.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = (
        config.attributes.get("database_url")
        or os.environ.get("FLOPPY_MIGRATION_DATABASE_URL")
        or os.environ.get("FLOPPY_DATABASE_URL")
    )
    if not url:
        raise RuntimeError("set FLOPPY_MIGRATION_DATABASE_URL (the owner role) to run migrations")
    return str(url)


def run_migrations_offline() -> None:
    """Emits the SQL instead of running it."""
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_server_default=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
