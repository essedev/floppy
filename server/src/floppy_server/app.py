"""
The FastAPI application: routers under `/api/v1`, error handlers, the origin check, and the
background loops in the lifespan.

The admin web is served from the same origin (a reverse proxy in production, Vite's proxy in
development), so the session cookie is `SameSite=Lax` and unsafe requests that carry an
`Origin` must come from `FLOPPY_PUBLIC_URL`. The app and devices send no `Origin`.
"""

import asyncio
import contextlib
import logging
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from floppy_server import background
from floppy_server.access.router import router as access_router
from floppy_server.audit.router import router as audit_router
from floppy_server.auth.router import router as auth_router
from floppy_server.config import Backend, Environment, Settings
from floppy_server.context import Context
from floppy_server.crypto import SecretBox
from floppy_server.db import make_engine, make_sessionmaker
from floppy_server.device.router import router as device_router
from floppy_server.devices.router import router as devices_router
from floppy_server.disks.router import router as disks_router
from floppy_server.errors import install_error_handlers
from floppy_server.health.router import router as health_router
from floppy_server.infra.juicefs import FakeVolumeFormatter, JuiceFSFormatter
from floppy_server.infra.mail import ConsoleMailer, SmtpMailer
from floppy_server.infra.meta import FakeMetaService, MetaService, PoolMetaService, parse_pool
from floppy_server.infra.metadata import FakeMetadataAdmin, ValkeyMetadataAdmin
from floppy_server.infra.storage import FakeStorageProber, S3StorageProber
from floppy_server.keys.router import router as keys_router
from floppy_server.paths import API_PREFIX
from floppy_server.people.router import router as people_router
from floppy_server.settings.router import router as settings_router

log = logging.getLogger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def build_meta(settings: Settings) -> MetaService:
    """The meta service the settings name: the pool of `FLOPPY_META_POOL`, or the fake."""
    if settings.meta_backend == "fake":
        return FakeMetaService()
    password = settings.valkey_admin_password
    return PoolMetaService(
        parse_pool(settings.meta_pool),
        password.get_secret_value() if password else None,
        settings.valkey_admin_username,
    )


def build_context(settings: Settings) -> Context:
    """The process's context from its settings: real or fake external systems as configured."""
    engine = make_engine(settings.database_url.get_secret_value(), pool_size=settings.db_pool_size)
    return Context(
        settings=settings,
        engine=engine,
        sessions=make_sessionmaker(engine),
        box=SecretBox(settings.key_ring),
        mailer=ConsoleMailer() if settings.mail_backend == "console" else SmtpMailer(),
        metadata=ValkeyMetadataAdmin()
        if settings.metadata_backend is Backend.REAL
        else FakeMetadataAdmin(),
        meta=build_meta(settings),
        formatter=JuiceFSFormatter(
            shlex.split(settings.juicefs_command), settings.juicefs_timeout_seconds
        )
        if settings.juicefs_backend is Backend.REAL
        else FakeVolumeFormatter(),
        prober=S3StorageProber(settings.storage_timeout_seconds)
        if settings.storage_backend is Backend.REAL
        else FakeStorageProber(),
    )


def create_app(ctx: Context) -> FastAPI:
    """The application around a context."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks = background.start(ctx) if ctx.settings.background_tasks else []
        if ctx.settings.juicefs_backend is Backend.FAKE:
            log.warning("JuiceFS is faked: disks are recorded but not formatted")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await ctx.engine.dispose()

    production = ctx.settings.env is Environment.PRODUCTION
    app = FastAPI(
        title="Floppy control plane",
        version="0.1.0",
        lifespan=lifespan,
        # The OpenAPI document and its pages are for development: off in production.
        openapi_url=None if production else f"{API_PREFIX}/openapi.json",
        docs_url=None if production else f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.ctx = ctx
    install_error_handlers(app)

    @app.middleware("http")
    async def same_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")
        if (
            request.method in UNSAFE_METHODS
            and origin is not None
            and origin != ctx.settings.public_origin
        ):
            return JSONResponse(
                {"code": "forbidden", "message": "Cross-origin request refused", "details": {}},
                status_code=403,
            )
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    api = APIRouter(prefix=API_PREFIX)
    for router in (
        auth_router,
        people_router,
        devices_router,
        disks_router,
        access_router,
        keys_router,
        audit_router,
        health_router,
        settings_router,
        device_router,
    ):
        api.include_router(router)
    app.include_router(api)
    return app
