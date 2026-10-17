"""
The meta service's HTTP API, on the private network only: the control plane creates,
reconfigures, stops and reads instances with its bearer token; Traefik reads the routes with
the routes token. Nothing here is routed to the internet.
"""

import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from floppy_meta.config import Settings
from floppy_meta.routes import device_url, traefik_config
from floppy_meta.store import NAME, Instance
from floppy_meta.supervisor import NotFoundError, Status, Supervisor, SupervisorError

MaxMemory = Annotated[int | None, Field(ge=16, le=1_048_576)]


class CreateRequest(BaseModel):
    """Options of a new instance; an existing one keeps its own."""

    maxmemory_mb: MaxMemory = None


class ReconfigureRequest(BaseModel):
    """What to change on an instance: fields left out stay as they are."""

    maxmemory_mb: MaxMemory = None
    rotate_admin_password: bool = False


class StopRequest(BaseModel):
    """`discard` moves the instance's data to the trash; otherwise it stays to start again."""

    discard: bool = False


class InstanceSpec(BaseModel):
    """An instance with its admin credentials: only in answers that create or change it."""

    name: str
    label: str
    url: str
    port: int
    admin_username: str | None
    admin_password: str
    maxmemory_mb: int | None


class InstanceStatus(BaseModel):
    """An instance as status and listings show it, without credentials."""

    name: str
    label: str
    url: str
    port: int
    desired: str
    state: str
    pid: int | None
    restarts: int
    last_exit_code: int | None
    uptime_seconds: float | None
    maxmemory_mb: int | None
    created_at: str
    details: dict[str, Any] = Field(default_factory=dict)


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def create_app(settings: Settings, supervisor: Supervisor | None = None) -> FastAPI:
    """The API over a supervisor, which the lifespan starts and stops."""
    sup = supervisor or Supervisor(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await sup.start()
        try:
            yield
        finally:
            await sup.shutdown()

    app = FastAPI(title="Floppy meta service", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.supervisor = sup

    def require(secret: str) -> Callable[[Request], Awaitable[None]]:
        async def check(request: Request) -> None:
            if not hmac.compare_digest(_bearer(request).encode(), secret.encode()):
                raise HTTPException(status_code=401, detail="unauthorized")

        return check

    control = Depends(require(settings.token.get_secret_value()))
    routes = Depends(require(settings.routes_token.get_secret_value()))

    def spec(instance: Instance) -> InstanceSpec:
        return InstanceSpec(
            name=instance.name,
            label=instance.label,
            url=device_url(settings, instance),
            port=instance.port,
            admin_username=None,
            admin_password=instance.admin_password,
            maxmemory_mb=instance.maxmemory_mb,
        )

    def status(
        instance: Instance, st: Status, details: dict[str, Any] | None = None
    ) -> InstanceStatus:
        return InstanceStatus(
            name=instance.name,
            label=instance.label,
            url=device_url(settings, instance),
            port=instance.port,
            desired=instance.desired,
            state=st.state,
            pid=st.pid,
            restarts=st.restarts,
            last_exit_code=st.last_exit_code,
            uptime_seconds=st.uptime_seconds,
            maxmemory_mb=instance.maxmemory_mb,
            created_at=instance.created_at,
            details=details or {},
        )

    def check_name(name: str) -> None:
        if not NAME.match(name):
            raise HTTPException(status_code=422, detail="not an instance name")

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(SupervisorError)
    async def failed(_: Request, error: SupervisorError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        everything = sup.all()
        wanted = [s for i, s in everything if i.desired == "running"]
        return {
            "ok": True,
            "instances": len(everything),
            "wanted": len(wanted),
            "running": sum(1 for s in wanted if s.state == "running"),
        }

    @app.put("/v1/instances/{name}", dependencies=[control])
    async def ensure(
        name: str, response: Response, body: CreateRequest | None = None
    ) -> InstanceSpec:
        check_name(name)
        instance, created = await sup.ensure(name, (body or CreateRequest()).maxmemory_mb)
        response.status_code = 201 if created else 200
        return spec(instance)

    @app.get("/v1/instances", dependencies=[control])
    async def listing() -> list[InstanceStatus]:
        return [status(i, s) for i, s in sup.all()]

    @app.get("/v1/instances/{name}", dependencies=[control])
    async def read(name: str) -> InstanceStatus:
        check_name(name)
        instance, st = sup.get(name)
        return status(instance, st, await sup.details(name))

    @app.patch("/v1/instances/{name}", dependencies=[control])
    async def reconfigure(name: str, body: ReconfigureRequest) -> InstanceSpec:
        check_name(name)
        instance = await sup.reconfigure(
            name,
            maxmemory_mb=body.maxmemory_mb,
            change_maxmemory="maxmemory_mb" in body.model_fields_set,
            rotate=body.rotate_admin_password,
        )
        return spec(instance)

    @app.post("/v1/instances/{name}/stop", dependencies=[control])
    async def stop(name: str, body: StopRequest | None = None) -> dict[str, str]:
        check_name(name)
        discard = (body or StopRequest()).discard
        await sup.stop(name, discard=discard)
        return {"state": "discarded" if discard else "stopped"}

    @app.post("/v1/instances/{name}/start", dependencies=[control])
    async def start(name: str) -> InstanceSpec:
        check_name(name)
        return spec(await sup.start_instance(name))

    @app.get("/v1/traefik", dependencies=[routes])
    async def traefik() -> dict[str, Any]:
        return traefik_config(settings, [i for i, _ in sup.all()])

    return app
