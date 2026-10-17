"""`/settings`: the organization's name, SMTP server and default endpoint, a mail test, a bucket probe (admins only)."""

from fastapi import APIRouter, Response

from floppy_server.api import AdminDep, CamelModel, CtxDep
from floppy_server.settings import service
from floppy_server.settings.service import SettingsOut, StorageProbeIn, UpdateSettingsRequest

router = APIRouter(tags=["settings"])


class MailTestResponse(CamelModel):
    """Where the test mail went."""

    sent_to: str


@router.get("/settings", response_model=SettingsOut)
async def get_settings(actor: AdminDep, ctx: CtxDep) -> SettingsOut:
    """The organization's settings."""
    return await service.get_settings(ctx, actor.organization_id)


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(body: UpdateSettingsRequest, actor: AdminDep, ctx: CtxDep) -> SettingsOut:
    """Changes the settings; the default endpoint only while no disk exists."""
    return await service.update_settings(ctx, actor, body)


@router.post("/settings/mail/test", response_model=MailTestResponse)
async def test_mail(actor: AdminDep, ctx: CtxDep) -> MailTestResponse:
    """Sends a test mail to the admin."""
    return MailTestResponse(sent_to=await service.test_mail(ctx, actor))


@router.post("/settings/storage/probe", status_code=204)
async def probe_storage(body: StorageProbeIn, actor: AdminDep, ctx: CtxDep) -> Response:
    """A test write with a pasted bucket and key before creating a disk (manual)."""
    await service.probe_storage(ctx, actor, body)
    return Response(status_code=204)
