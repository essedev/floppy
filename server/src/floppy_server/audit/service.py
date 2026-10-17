"""
The audit log: written by the services in the same transaction as the change it records,
read by `GET /audit`.

Each action has a fixed category and an allowlist of detail keys
(docs/DATABASE_SCHEMA.md, "Audit log"): a detail key outside the list is a programming
error, so a secret can never slip into the log through a careless dict.
"""

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.context import utcnow
from floppy_server.errors import validation
from floppy_server.models import AuditEvent

DetailValue = str | int | list[str] | list[int] | list[str | int]

ACTIONS: dict[str, tuple[str, frozenset[str]]] = {
    "signed_in": ("access", frozenset({"via", "kind"})),
    "sign_in_locked": ("access", frozenset()),
    "person_invited": ("people", frozenset({"role"})),
    "invite_resent": ("people", frozenset({"role"})),
    "invite_accepted": ("people", frozenset()),
    "role_changed": ("people", frozenset({"from", "to"})),
    "person_removed": ("people", frozenset({"macs"})),
    "mac_registered": ("macs", frozenset({"appVersion", "macosVersion", "reissued"})),
    "mac_revoked": ("macs", frozenset({"reason"})),
    "mac_signed_out": ("macs", frozenset()),
    "mac_seen_outdated": ("macs", frozenset({"appVersion"})),
    "key_rotation_started": ("key", frozenset({"newKeyTail", "oldKeyTail"})),
    "key_rotated": ("key", frozenset({"disks"})),
    "key_rotation_failed": ("key", frozenset({"step", "code"})),
    "key_window_closed": ("key", frozenset({"oldKeyTail", "reasons"})),
    "disk_created": ("disks", frozenset({"blockSize"})),
    "disk_updated": ("disks", frozenset({"trashDays", "cacheExpireDays"})),
    "settings_updated": ("settings", frozenset({"fields"})),
}

SEARCH_DAYS_MAX = 90


@dataclass(frozen=True)
class AuditActor:
    """Who did it: a person (with their id and name at that moment), the system or an operator."""

    kind: str
    user_id: UUID | None = None
    label: str | None = None

    @staticmethod
    def person(user_id: UUID, label: str) -> AuditActor:
        return AuditActor(kind="person", user_id=user_id, label=label)

    @staticmethod
    def system() -> AuditActor:
        return AuditActor(kind="system")


@dataclass(frozen=True)
class Target:
    """What the action was done to, with its name at that moment."""

    kind: str
    id: UUID
    label: str


def record(
    db: AsyncSession,
    organization_id: UUID,
    action: str,
    actor: AuditActor,
    target: Target,
    detail: Mapping[str, DetailValue] | None = None,
) -> AuditEvent:
    """Adds one audit row to the session's transaction."""
    category, allowed = ACTIONS[action]
    detail = detail or {}
    unexpected = set(detail) - allowed
    if unexpected:
        raise ValueError(f"audit detail keys not allowed for {action}: {sorted(unexpected)}")
    event = AuditEvent(
        organization_id=organization_id,
        occurred_at=utcnow(),
        actor_kind=actor.kind,
        actor_user_id=actor.user_id,
        actor_label=actor.label,
        action=action,
        category=category,
        target_kind=target.kind,
        target_id=target.id,
        target_label=target.label,
        detail=dict(detail),
    )
    db.add(event)
    return event


def encode_cursor(event: AuditEvent) -> str:
    """The keyset cursor after an event: its time and id."""
    raw = f"{event.occurred_at.isoformat()}|{event.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """The time and id a cursor points after; 422 when it was not made here."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, event_id = raw.partition("|")
        return datetime.fromisoformat(at), UUID(event_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise validation("Invalid cursor") from error


async def list_events(
    db: AsyncSession,
    organization_id: UUID,
    *,
    since_days: int,
    category: str | None,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AuditEvent], str | None]:
    """A page of the organization's log, newest first, and the cursor of the next page."""
    since = utcnow() - timedelta(days=min(since_days, SEARCH_DAYS_MAX))
    query = select(AuditEvent).where(
        AuditEvent.organization_id == organization_id, AuditEvent.occurred_at >= since
    )
    if category:
        query = query.where(AuditEvent.category == category)
    needle = (q or "").strip()
    if needle:
        pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(
                AuditEvent.actor_label.ilike(pattern, escape="\\"),
                AuditEvent.target_label.ilike(pattern, escape="\\"),
            )
        )
    if cursor:
        at, event_id = decode_cursor(cursor)
        query = query.where(tuple_(AuditEvent.occurred_at, AuditEvent.id) < tuple_(at, event_id))
    rows = list(
        (
            await db.execute(
                query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
            )
        ).scalars()
    )
    if len(rows) > limit:
        return rows[:limit], encode_cursor(rows[limit - 1])
    return rows, None
