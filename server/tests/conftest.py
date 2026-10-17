"""
Integration fixtures: a real PostgreSQL 18 database built by the migrations, the
application connected as the role without DELETE, and fakes only for what leaves the
process (mail, Valkey unless a test asks for the real one, JuiceFS, the storage probe).

The database is the Compose service (`docker compose up -d db valkey`); credentials come from
the environment (`uv run --env-file .env pytest`). Each session recreates `floppy_test`, and
each test starts from empty tables, truncated by the owner role.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from floppy_server.app import create_app
from floppy_server.bootstrap import BootstrapInput, bootstrap
from floppy_server.config import Environment, MailBackend, Settings
from floppy_server.context import Context
from floppy_server.crypto import SecretBox
from floppy_server.db import Base, make_engine, make_sessionmaker
from floppy_server.infra.juicefs import FakeVolumeFormatter
from floppy_server.infra.metadata import FakeMetadataAdmin
from floppy_server.infra.storage import FakeStorageProber
from tests.helpers import RecordingMailer, sign_in

SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB = "floppy_test"
SECRET_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        pytest.exit(f"{name} is not set: run the tests with `uv run --env-file .env pytest`", 2)
    return value


DB_HOST = _env("FLOPPY_TEST_DB_HOST", "127.0.0.1")
DB_PORT = _env("FLOPPY_TEST_DB_PORT", "4504")
VALKEY_URL = _env("FLOPPY_TEST_VALKEY_URL", "redis://127.0.0.1:4505")


def owner_url(database: str = TEST_DB) -> str:
    """The owner role's URL (migrations, truncation, time travel in tests)."""
    password = _env("FLOPPY_DB_OWNER_PASSWORD")
    return f"postgresql+asyncpg://floppy_owner:{password}@{DB_HOST}:{DB_PORT}/{database}"


def app_url() -> str:
    """The application role's URL: no DELETE, no TRUNCATE."""
    password = _env("FLOPPY_DB_APP_PASSWORD")
    return f"postgresql+asyncpg://floppy_app:{password}@{DB_HOST}:{DB_PORT}/{TEST_DB}"


def _migrate() -> None:
    config = Config(os.path.join(SERVER_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(SERVER_ROOT, "migrations"))
    config.attributes["database_url"] = owner_url()
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
async def database() -> None:
    """A fresh `floppy_test` database, built by the migrations."""
    dsn = owner_url("floppy").replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
        await conn.execute(f"GRANT CONNECT ON DATABASE {TEST_DB} TO floppy_app")
    finally:
        await conn.close()
    await asyncio.to_thread(_migrate)


@pytest.fixture(scope="session")
async def owner_engine(database: None) -> AsyncIterator[AsyncEngine]:
    """The owner's engine, for setup and time travel."""
    engine = create_async_engine(owner_url(), connect_args={"server_settings": {"timezone": "UTC"}})
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def app_engine(database: None) -> AsyncIterator[AsyncEngine]:
    """The application's engine, as the role without DELETE."""
    engine = make_engine(app_url(), pool_size=5)
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean(owner_engine: AsyncEngine) -> None:
    """Empty tables, truncated by the owner (the only role the audit trigger lets through)."""
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


def make_settings(**overrides: object) -> Settings:
    """Test settings: plain http cookies, no background loops, several organizations allowed."""
    values: dict[str, object] = {
        "env": Environment.TEST,
        "database_url": app_url(),
        "secret_key": SECRET_KEY,
        "public_url": "http://localhost:4508",
        "cookie_secure": False,
        "mail_backend": MailBackend.SMTP,
        "smtp_host": "smtp.test",
        "background_tasks": False,
        "single_organization": False,
        "latest_app_version": "0.4.0",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
async def ctx(app_engine: AsyncEngine, clean: None) -> Context:
    """A context on the test database with in-memory fakes for everything outside."""
    settings = make_settings()
    return Context(
        settings=settings,
        engine=app_engine,
        sessions=make_sessionmaker(app_engine),
        box=SecretBox(settings.key_ring),
        mailer=RecordingMailer(),
        metadata=FakeMetadataAdmin(),
        formatter=FakeVolumeFormatter(),
        prober=FakeStorageProber(),
    )


ClientFactory = Callable[[], httpx.AsyncClient]


@pytest.fixture
async def clients(ctx: Context) -> AsyncIterator[ClientFactory]:
    """Makes API clients, each with its own cookie jar (one per browser)."""
    app = create_app(ctx)
    made: list[httpx.AsyncClient] = []

    def factory() -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver/api/v1"
        )
        made.append(client)
        return client

    yield factory
    for client in made:
        await client.aclose()


@pytest.fixture
async def client(clients: ClientFactory) -> httpx.AsyncClient:
    """One API client, signed out."""
    return clients()


@dataclass(frozen=True)
class Org:
    """An organization made by the bootstrap, with its admin's address."""

    id: UUID
    admin_email: str
    bucket: str
    valkey_url: str


async def make_org(
    ctx: Context,
    slug: str,
    admin_email: str,
    *,
    key: str = "AKIAFIRSTKEY00001",
    valkey_url: str | None = None,
) -> Org:
    """An organization through the real bootstrap."""
    bucket = f"bucket-{slug}"
    valkey = valkey_url or f"redis://valkey-{slug}.test:6379"
    org_id = await bootstrap(
        ctx,
        BootstrapInput(
            organization_name=f"Studio {slug}",
            slug=slug,
            admin_email=admin_email,
            s3_endpoint="https://s3.test",
            s3_region="eu-south",
            s3_bucket=bucket,
            s3_access_key_id=key,
            s3_secret_key="first-secret-key-value-0001",
            valkey_url=valkey,
        ),
    )
    return Org(id=org_id, admin_email=admin_email, bucket=bucket, valkey_url=valkey)


@pytest.fixture
async def org(ctx: Context) -> Org:
    """The organization most tests act in."""
    return await make_org(ctx, "studio-a", "giulia.admin@studio-a.test")


@pytest.fixture
async def admin(org: Org, ctx: Context, clients: ClientFactory) -> httpx.AsyncClient:
    """A client signed in as the organization's admin."""
    client = clients()
    await sign_in(client, ctx, org.admin_email)
    return client
