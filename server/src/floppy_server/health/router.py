"""
`GET /health`: the organization's services as the control plane last saw them (admins
only), each disk's instance and bucket and a summary of them, and `GET /healthz`: the process
and its database, for the container's health check.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select, text

from floppy_server.api import AdminDep, CamelModel, CtxDep
from floppy_server.context import Context
from floppy_server.errors import not_found
from floppy_server.keys.schemas import Versioning
from floppy_server.models import Organization, OrganizationStatus, Volume, VolumeStatus

router = APIRouter(tags=["health"])


class MetadataHealth(CamelModel):
    """An instance's last ping, or the worst of every disk's."""

    state: Literal["ok", "down"]
    latency_ms: int | None
    checked_at: datetime | None


class StorageHealth(CamelModel):
    """A bucket's last test write and the versioning it read, or the worst of every disk's."""

    state: Literal["ok", "failing", "unknown"]
    last_probe_at: datetime | None
    versioning: Versioning


class MailHealth(CamelModel):
    """The last mail handed to the SMTP server."""

    state: Literal["ok", "unconfigured", "failing"]
    last_sent_at: datetime | None


class DiskHealth(CamelModel):
    """One disk's instance and bucket."""

    id: UUID
    name: str
    metadata: MetadataHealth
    storage: StorageHealth


class HealthOut(CamelModel):
    """The organization's services: `metadata` and `storage` summarize `disks`."""

    metadata: MetadataHealth
    storage: StorageHealth
    mail: MailHealth
    disks: list[DiskHealth]


def _disk(volume: Volume, status: VolumeStatus | None) -> DiskHealth:
    return DiskHealth(
        id=volume.id,
        name=volume.name,
        metadata=MetadataHealth(
            state="ok" if status is None or status.reachable else "down",
            latency_ms=status.meta_latency_ms if status else None,
            checked_at=status.checked_at if status else None,
        ),
        storage=StorageHealth(
            state=status.storage_state if status else "unknown",
            last_probe_at=status.storage_probed_at if status else None,
            versioning=Versioning(
                state=status.bucket_versioning if status else "unknown",
                old_versions_days=status.bucket_old_versions_days if status else None,
            ),
        ),
    )


def _summary(disks: list[DiskHealth]) -> tuple[MetadataHealth, StorageHealth]:
    """The worst state and the highest latency; versioning only when every disk agrees."""
    latencies = [d.metadata.latency_ms for d in disks if d.metadata.latency_ms is not None]
    checked = [d.metadata.checked_at for d in disks if d.metadata.checked_at is not None]
    probed = [d.storage.last_probe_at for d in disks if d.storage.last_probe_at is not None]
    states = {d.storage.state for d in disks}
    storage_state: Literal["ok", "failing", "unknown"] = (
        "failing" if "failing" in states else "ok" if states == {"ok"} else "unknown"
    )
    versionings = {
        (d.storage.versioning.state, d.storage.versioning.old_versions_days) for d in disks
    }
    versioning = (
        Versioning(state=next(iter(versionings))[0], old_versions_days=next(iter(versionings))[1])
        if len(versionings) == 1
        else Versioning(state="unknown", old_versions_days=None)
    )
    return (
        MetadataHealth(
            state="down" if any(d.metadata.state == "down" for d in disks) else "ok",
            latency_ms=max(latencies) if latencies else None,
            checked_at=min(checked) if checked else None,
        ),
        StorageHealth(
            state=storage_state,
            last_probe_at=min(probed) if probed else None,
            versioning=versioning,
        ),
    )


async def health_view(
    ctx: Context, org: Organization, status: OrganizationStatus, disks: list[DiskHealth]
) -> HealthOut:
    """The API shape of the organization's status rows."""
    mail_state = status.mail_state
    if not ctx.mail_configured(org):
        mail_state = "unconfigured"
    elif mail_state == "unconfigured":
        mail_state = "ok"
    metadata, storage = _summary(disks)
    return HealthOut(
        metadata=metadata,
        storage=storage,
        mail=MailHealth(state=mail_state, last_sent_at=status.mail_sent_at),
        disks=disks,
    )


@router.get("/health", response_model=HealthOut)
async def get_health(actor: AdminDep, ctx: CtxDep) -> HealthOut:
    """Each disk's instance and bucket, and mail, as last observed."""
    async with ctx.sessions() as db:
        org = await db.get(Organization, actor.organization_id)
        status = await db.get(OrganizationStatus, actor.organization_id)
        rows = (
            await db.execute(
                select(Volume, VolumeStatus)
                .outerjoin(
                    VolumeStatus,
                    (VolumeStatus.volume_id == Volume.id)
                    & (VolumeStatus.organization_id == Volume.organization_id),
                )
                .where(
                    Volume.organization_id == actor.organization_id,
                    Volume.state == "ready",
                    Volume.deleted_at.is_(None),
                )
                .order_by(Volume.created_at)
            )
        ).all()
    if org is None or status is None:
        raise not_found()
    return await health_view(ctx, org, status, [_disk(v, s) for v, s in rows])


class Liveness(CamelModel):
    """The process answers and reaches its database."""

    status: Literal["ok"]


@router.get("/healthz", response_model=Liveness, include_in_schema=False)
async def healthz(ctx: CtxDep) -> Liveness:
    """For Docker's health check: no session, no organization data."""
    async with ctx.sessions() as db:
        await db.execute(text("SELECT 1"))
    return Liveness(status="ok")
