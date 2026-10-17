"""`/people`: invite, resend, change role, remove (admins only)."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from floppy_server.access.service import DiskEntry
from floppy_server.api import AdminDep, CamelModel, CtxDep, Email
from floppy_server.errors import not_found
from floppy_server.keys.schemas import KeyStatusOut
from floppy_server.keys.service import key_status
from floppy_server.models import Organization
from floppy_server.people import service
from floppy_server.people.service import PersonOut

router = APIRouter(tags=["people"])


class InviteRequest(CamelModel):
    """An address, a role, and the disks the person will see (none unless listed)."""

    email: Email
    role: Literal["admin", "member"]
    disks: list[DiskEntry] = Field(default_factory=list, max_length=500)


class UpdatePersonRequest(CamelModel):
    """A new role."""

    role: Literal["admin", "member"]


class RemovePersonResponse(CamelModel):
    """The removed person, how many devices went with them, and the key status after it."""

    person: PersonOut
    revoked_devices: int
    key: KeyStatusOut


@router.get("/people", response_model=list[PersonOut])
async def list_people(actor: AdminDep, ctx: CtxDep) -> list[PersonOut]:
    """Everyone in the organization, removed people included."""
    return await service.list_people(ctx, actor)


@router.post("/people", response_model=PersonOut, status_code=201)
async def invite(body: InviteRequest, actor: AdminDep, ctx: CtxDep) -> PersonOut:
    """Invites someone by mail."""
    return await service.invite(ctx, actor, body.email, body.role, body.disks)


@router.patch("/people/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: UUID, body: UpdatePersonRequest, actor: AdminDep, ctx: CtxDep
) -> PersonOut:
    """Changes a person's role."""
    return await service.change_role(ctx, actor, person_id, body.role)


@router.delete("/people/{person_id}", response_model=RemovePersonResponse)
async def remove_person(person_id: UUID, actor: AdminDep, ctx: CtxDep) -> RemovePersonResponse:
    """Removes a person and revokes their devices."""
    person, revoked = await service.remove(ctx, actor, person_id)
    async with ctx.sessions() as db:
        org = await db.get(Organization, actor.organization_id)
        if org is None:
            raise not_found()
        key = await key_status(db, org)
    return RemovePersonResponse(person=person, revoked_devices=revoked, key=key)


@router.post("/people/{person_id}/resend", response_model=PersonOut)
async def resend(person_id: UUID, actor: AdminDep, ctx: CtxDep) -> PersonOut:
    """Sends a new invitation link."""
    return await service.resend(ctx, actor, person_id)
