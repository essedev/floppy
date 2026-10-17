"""`GET /audit`: the organization's log, filtered and cursor-paginated (admins only)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from floppy_server.api import AdminDep, CamelModel, CtxDep
from floppy_server.audit.service import list_events
from floppy_server.errors import validation
from floppy_server.models import AuditEvent

router = APIRouter(tags=["audit"])

SINCE_DAYS = (7, 30, 90)


class AuditActorOut(CamelModel):
    """Who acted: a person (with id and name then), the system or an operator."""

    kind: Literal["person", "system", "operator"]
    id: UUID | None
    label: str | None


class AuditTargetOut(CamelModel):
    """What the action was done to."""

    kind: str
    id: UUID
    label: str


class AuditEventOut(CamelModel):
    """One audit row."""

    id: UUID
    at: datetime
    actor: AuditActorOut
    action: str
    category: str
    target: AuditTargetOut
    detail: dict[str, object]


class AuditPage(CamelModel):
    """A page of the log."""

    items: list[AuditEventOut]
    next_cursor: str | None


def to_out(event: AuditEvent) -> AuditEventOut:
    """The API shape of an audit row."""
    actor = AuditActorOut(kind=event.actor_kind, id=event.actor_user_id, label=event.actor_label)
    return AuditEventOut(
        id=event.id,
        at=event.occurred_at,
        actor=actor,
        action=event.action,
        category=event.category,
        target=AuditTargetOut(kind=event.target_kind, id=event.target_id, label=event.target_label),
        detail=event.detail,
    )


@router.get("/audit", response_model=AuditPage)
async def get_audit(
    actor: AdminDep,
    ctx: CtxDep,
    since_days: Annotated[int, Query(alias="sinceDays")] = 30,
    category: Annotated[
        Literal["access", "people", "macs", "key", "disks", "settings"] | None, Query()
    ] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AuditPage:
    """The log, newest first."""
    if since_days not in SINCE_DAYS:
        raise validation("sinceDays must be 7, 30 or 90", {"field": "sinceDays"})
    async with ctx.sessions() as db:
        events, next_cursor = await list_events(
            db,
            actor.organization_id,
            since_days=since_days,
            category=category,
            q=q,
            cursor=cursor,
            limit=limit,
        )
    return AuditPage(items=[to_out(e) for e in events], next_cursor=next_cursor)
