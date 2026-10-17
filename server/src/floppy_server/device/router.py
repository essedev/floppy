"""`/device/*`: the app's endpoints, authenticated by `Authorization: Bearer <device token>`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from floppy_server.api import CtxDep
from floppy_server.device import service
from floppy_server.device.service import (
    DeviceConfig,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
)
from floppy_server.errors import unauthorized

router = APIRouter(tags=["device"])
bearer = HTTPBearer(auto_error=False)


def device_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    """The device token from the Authorization header, or 401."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise unauthorized("Missing device token")
    return credentials.credentials


TokenDep = Annotated[str, Depends(device_token)]


@router.post("/device/register", response_model=RegisterResponse)
async def register(body: RegisterRequest, token: TokenDep, ctx: CtxDep) -> RegisterResponse:
    """Creates the Mac's Valkey user; the password comes back once."""
    return await service.register(ctx, token, body)


@router.get("/device/config", response_model=DeviceConfig)
async def config(token: TokenDep, ctx: CtxDep) -> DeviceConfig:
    """The Valkey address and the disks to mount."""
    return await service.config(ctx, token)


@router.post("/device/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(body: HeartbeatRequest, token: TokenDep, ctx: CtxDep) -> HeartbeatResponse:
    """The Mac's report; the answer says whether it is still allowed."""
    return await service.heartbeat(ctx, token, body)


@router.post("/device/sign-out", status_code=204)
async def sign_out(token: TokenDep, ctx: CtxDep) -> Response:
    """The Mac signs out: revoked by its owner, without opening the key window."""
    await service.sign_out(ctx, token)
    return Response(status_code=204)
