"""
`GET /health`: the organization's services as the control plane last saw them (admins
only), and `GET /healthz`: the process and its database, for the container's health check.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text

from floppy_server.api import AdminDep, CamelModel, CtxDep
from floppy_server.context import Context
from floppy_server.errors import not_found
from floppy_server.keys.schemas import Versioning
from floppy_server.models import Organization, OrganizationStatus

router = APIRouter(tags=["health"])


class MetadataHealth(CamelModel):
    """The central Valkey's last ping."""

    state: Literal["ok", "down"]
    latency_ms: int | None
    checked_at: datetime


class StorageHealth(CamelModel):
    """The last test write, and the bucket's versioning it read."""

    state: Literal["ok", "failing"]
    last_probe_at: datetime | None
    versioning: Versioning


class MailHealth(CamelModel):
    """The last mail handed to the SMTP server."""

    state: Literal["ok", "unconfigured", "failing"]
    last_sent_at: datetime | None


class HealthOut(CamelModel):
    """The organization's services."""

    metadata: MetadataHealth
    storage: StorageHealth
    mail: MailHealth


async def health_view(ctx: Context, org: Organization, status: OrganizationStatus) -> HealthOut:
    """The API shape of the organization's status row."""
    mail_state = status.mail_state
    if not ctx.mail_configured(org):
        mail_state = "unconfigured"
    elif mail_state == "unconfigured":
        mail_state = "ok"
    return HealthOut(
        metadata=MetadataHealth(
            state=status.metadata_state,
            latency_ms=status.metadata_latency_ms,
            checked_at=status.metadata_checked_at or status.updated_at,
        ),
        storage=StorageHealth(
            state=status.storage_state,
            last_probe_at=status.storage_probed_at,
            versioning=Versioning(
                state=status.bucket_versioning,
                old_versions_days=status.bucket_old_versions_days,
            ),
        ),
        mail=MailHealth(state=mail_state, last_sent_at=status.mail_sent_at),
    )


@router.get("/health", response_model=HealthOut)
async def get_health(actor: AdminDep, ctx: CtxDep) -> HealthOut:
    """Metadata, storage and mail, as last observed."""
    async with ctx.sessions() as db:
        org = await db.get(Organization, actor.organization_id)
        status = await db.get(OrganizationStatus, actor.organization_id)
    if org is None or status is None:
        raise not_found()
    return await health_view(ctx, org, status)


class Liveness(CamelModel):
    """The process answers and reaches its database."""

    status: Literal["ok"]


@router.get("/healthz", response_model=Liveness, include_in_schema=False)
async def healthz(ctx: CtxDep) -> Liveness:
    """For Docker's health check: no session, no organization data."""
    async with ctx.sessions() as db:
        await db.execute(text("SELECT 1"))
    return Liveness(status="ok")
