"""`/access` and `/disks/{id}/access/{personId}`: who may see each disk, and how (admins only)."""

from uuid import UUID

from fastapi import APIRouter

from floppy_server.access import service
from floppy_server.access.service import DiskAccessOut, SetAccessRequest
from floppy_server.api import AdminDep, CtxDep

router = APIRouter(tags=["access"])


@router.get("/access", response_model=list[DiskAccessOut])
async def list_access(actor: AdminDep, ctx: CtxDep) -> list[DiskAccessOut]:
    """Every live disk and person with the person's access, `none` included."""
    return await service.list_access(ctx, actor.organization_id)


@router.put("/disks/{disk_id}/access/{person_id}", response_model=DiskAccessOut)
async def set_access(
    disk_id: UUID, person_id: UUID, body: SetAccessRequest, actor: AdminDep, ctx: CtxDep
) -> DiskAccessOut:
    """Sets a person's access to a disk, applied live to their devices."""
    return await service.set_access(ctx, actor, disk_id, person_id, body.access)
