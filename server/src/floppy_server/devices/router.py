"""`/devices`: the Macs of the organization (members see only their own) and revocation."""

from uuid import UUID

from fastapi import APIRouter

from floppy_server.api import ActorDep, CamelModel, CtxDep
from floppy_server.devices import service
from floppy_server.devices.service import DeviceOut
from floppy_server.keys.schemas import KeyStatusOut
from floppy_server.keys.service import key_status
from floppy_server.models import Organization

router = APIRouter(tags=["devices"])


class RevokeDeviceResponse(CamelModel):
    """The revoked device and the key status (its window may be open now); members get no key."""

    device: DeviceOut
    key: KeyStatusOut | None


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(actor: ActorDep, ctx: CtxDep) -> list[DeviceOut]:
    """Every device for an admin, only the caller's own for a member."""
    return await service.list_devices(ctx, actor)


@router.post("/devices/{device_id}/revoke", response_model=RevokeDeviceResponse)
async def revoke_device(device_id: UUID, actor: ActorDep, ctx: CtxDep) -> RevokeDeviceResponse:
    """Revokes a device: its Valkey user is deleted and its connections killed."""
    await service.revoke_device(ctx, actor, device_id)
    device = await service.get_device(ctx, actor.organization_id, device_id)
    key = None
    if actor.is_admin:
        async with ctx.sessions() as db:
            org = await db.get(Organization, actor.organization_id)
            if org is not None:
                key = await key_status(db, org)
    return RevokeDeviceResponse(device=device, key=key)
