"""`/disks`: list, create, update (admins only)."""

from uuid import UUID

from fastapi import APIRouter

from floppy_server.api import AdminDep, CtxDep
from floppy_server.disks import service
from floppy_server.disks.service import CreateDiskRequest, DiskOut, UpdateDiskRequest

router = APIRouter(tags=["disks"])


@router.get("/disks", response_model=list[DiskOut])
async def list_disks(actor: AdminDep, ctx: CtxDep) -> list[DiskOut]:
    """The organization's disks."""
    return await service.list_disks(ctx, actor)


@router.get("/disks/{disk_id}", response_model=DiskOut)
async def get_disk(disk_id: UUID, actor: AdminDep, ctx: CtxDep) -> DiskOut:
    """One disk, also after its creation failed (then out of the list)."""
    return await service.get_disk(ctx, actor.organization_id, disk_id)


@router.post("/disks", response_model=DiskOut, status_code=201)
async def create_disk(body: CreateDiskRequest, actor: AdminDep, ctx: CtxDep) -> DiskOut:
    """Records a disk as `creating`; poll `GET /disks/{id}` until `ready` or `failed`."""
    return await service.create_disk(ctx, actor, body)


@router.patch("/disks/{disk_id}", response_model=DiskOut)
async def update_disk(
    disk_id: UUID, body: UpdateDiskRequest, actor: AdminDep, ctx: CtxDep
) -> DiskOut:
    """Changes trash days or the cache expiry."""
    return await service.update_disk(ctx, actor, disk_id, body)
