"""`/macs`: the list (members see only their own) and revocation."""

from uuid import UUID

from fastapi import APIRouter

from floppy_server.api import ActorDep, CamelModel, CtxDep
from floppy_server.keys.schemas import KeyStatusOut
from floppy_server.keys.service import key_status
from floppy_server.macs import service
from floppy_server.macs.service import MacOut
from floppy_server.models import Organization

router = APIRouter(tags=["macs"])


class RevokeMacResponse(CamelModel):
    """The revoked Mac and the key status (its window may be open now); members get no key."""

    mac: MacOut
    key: KeyStatusOut | None


@router.get("/macs", response_model=list[MacOut])
async def list_macs(actor: ActorDep, ctx: CtxDep) -> list[MacOut]:
    """Every Mac for an admin, only the caller's own for a member."""
    return await service.list_macs(ctx, actor)


@router.post("/macs/{mac_id}/revoke", response_model=RevokeMacResponse)
async def revoke_mac(mac_id: UUID, actor: ActorDep, ctx: CtxDep) -> RevokeMacResponse:
    """Revokes a Mac: its Valkey user is deleted and its connections killed."""
    await service.revoke_mac(ctx, actor, mac_id)
    mac = await service.get_mac(ctx, actor.organization_id, mac_id)
    key = None
    if actor.is_admin:
        async with ctx.sessions() as db:
            org = await db.get(Organization, actor.organization_id)
            if org is not None:
                key = await key_status(db, org)
    return RevokeMacResponse(mac=mac, key=key)
