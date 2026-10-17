"""`/key`: each disk's S3 key, its window and its rotation (admins only)."""

from uuid import UUID

from fastapi import APIRouter

from floppy_server.api import AdminDep, CtxDep
from floppy_server.errors import not_found
from floppy_server.infra.juicefs import S3Credentials
from floppy_server.keys import service
from floppy_server.keys.schemas import (
    CloseRotationRequest,
    KeyStatusOut,
    RotationOut,
    StartRotationRequest,
)
from floppy_server.models import Organization

router = APIRouter(tags=["key"])


async def _status(ctx: CtxDep, organization_id: UUID) -> KeyStatusOut:
    async with ctx.sessions() as db:
        org = await db.get(Organization, organization_id)
        if org is None:
            raise not_found()
        return await service.key_status(db, org)


@router.get("/key", response_model=KeyStatusOut)
async def get_key(actor: AdminDep, ctx: CtxDep) -> KeyStatusOut:
    """Every disk's key, window and rotation, and the devices' keys."""
    return await _status(ctx, actor.organization_id)


@router.post("/key/rotations", response_model=RotationOut, status_code=201)
async def start_rotation(body: StartRotationRequest, actor: AdminDep, ctx: CtxDep) -> RotationOut:
    """Starts a rotation of one disk's key with a pasted new key (manual)."""
    rotation = await service.start_rotation(
        ctx,
        actor.organization_id,
        body.disk_id,
        user_id=actor.user_id,
        user_label=actor.label,
        key=S3Credentials(body.access_key_id, body.secret_key),
    )
    return await service.get_rotation(ctx, actor.organization_id, rotation.id)


@router.get("/key/rotations/{rotation_id}", response_model=RotationOut)
async def get_rotation(rotation_id: UUID, actor: AdminDep, ctx: CtxDep) -> RotationOut:
    """A rotation's progress."""
    return await service.get_rotation(ctx, actor.organization_id, rotation_id)


@router.post("/key/rotations/{rotation_id}/close", response_model=KeyStatusOut)
async def close_rotation(
    rotation_id: UUID, body: CloseRotationRequest, actor: AdminDep, ctx: CtxDep
) -> KeyStatusOut:
    """Closes a rotation once the disk's old key is deleted at the provider."""
    await service.close_rotation(
        ctx, actor.organization_id, rotation_id, actor.user_id, actor.label
    )
    return await _status(ctx, actor.organization_id)
