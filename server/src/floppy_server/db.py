"""
Database engine, sessions and the declarative base.

The engine pins the connection's time zone to UTC so nothing depends on the server's zone,
and every constraint is named by one convention so later migrations can drop them by name
(docs/DATABASE_SCHEMA.md, "Conventions").
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, declared_attr

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The base of every table, with the shared naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805 (a declarative directive)
        # Server-side values (generated columns, defaults, onupdate) come back with RETURNING
        # on the same statement: an async session cannot lazy-load them afterwards.
        return {"eager_defaults": True}


def make_engine(url: str, *, pool_size: int = 10) -> AsyncEngine:
    """An async engine on asyncpg with the connection in UTC."""
    return create_async_engine(
        url,
        pool_size=pool_size,
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC", "application_name": "floppy-server"}},
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """The session factory: no expiry on commit, so rows stay readable after a commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """A session inside one transaction, committed on success and rolled back on error."""
    async with sessions() as session, session.begin():
        yield session
