"""`/device/*`: the app's endpoints, authenticated by `Authorization: Bearer <device token>`."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from floppy_server.activity import service as activity_service
from floppy_server.activity.service import (
    ActivityAnswer,
    ActivityPage,
    ActivityReport,
    DeletesAnswer,
    DeletesRequest,
)
from floppy_server.api import CtxDep
from floppy_server.device import service
from floppy_server.device.service import (
    DeviceConfig,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
)
from floppy_server.errors import device_unauthorized

router = APIRouter(tags=["device"])
bearer = HTTPBearer(auto_error=False)


def device_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    """The device token from the Authorization header, or 401 `token_unknown`."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise device_unauthorized("token_unknown", "Missing device token")
    return credentials.credentials


TokenDep = Annotated[str, Depends(device_token)]


@router.post("/device/register", response_model=RegisterResponse)
async def register(body: RegisterRequest, token: TokenDep, ctx: CtxDep) -> RegisterResponse:
    """The bootstrap: credentials (once) and the disks the device may see."""
    return await service.register(ctx, token, body)


@router.get("/device/config", response_model=DeviceConfig)
async def config(token: TokenDep, ctx: CtxDep) -> DeviceConfig:
    """The disks the device may see, with their access and instances."""
    return await service.config(ctx, token)


@router.post("/device/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(body: HeartbeatRequest, token: TokenDep, ctx: CtxDep) -> HeartbeatResponse:
    """The device's report; the answer says what it may still see."""
    return await service.heartbeat(ctx, token, body)


@router.post("/device/sign-out", status_code=204)
async def sign_out(token: TokenDep, ctx: CtxDep) -> Response:
    """The device signs out: revoked by its owner, without opening a key window."""
    await service.sign_out(ctx, token)
    return Response(status_code=204)


@router.post("/device/activity", response_model=ActivityAnswer)
async def activity(body: ActivityReport, token: TokenDep, ctx: CtxDep) -> ActivityAnswer:
    """The device's activity on its disks; the answer is what changed on them since `after`."""
    return await activity_service.report(ctx, token, body)


@router.get("/device/activity", response_model=ActivityPage)
async def activity_history(
    token: TokenDep,
    ctx: CtxDep,
    disk_id: Annotated[UUID, Query(alias="diskId")],
    before: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ActivityPage:
    """A page of one disk's history, newest first."""
    return await activity_service.history(ctx, token, disk_id, before, limit)


@router.post("/device/activity/deletes", response_model=DeletesAnswer)
async def activity_deletes(body: DeletesRequest, token: TokenDep, ctx: CtxDep) -> DeletesAnswer:
    """Who deleted or replaced each of a disk's trash entries."""
    return await activity_service.deletes(ctx, token, body)
