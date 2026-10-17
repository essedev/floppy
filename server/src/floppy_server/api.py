"""
What the routers share: the camelCase schema base, the context, and who is calling.

A web request is authenticated by the session cookie and resolves to an `Actor` in exactly
one organization; the role is read from the membership on every request, so a role change
or a removal applies at once. Device requests are authenticated in `device/`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select, update

from floppy_server.context import Context, utcnow
from floppy_server.crypto import token_hash
from floppy_server.errors import forbidden, unauthorized
from floppy_server.models import Membership, Organization, User, WebSession
from floppy_server.ratelimit import rate_limited

SESSION_COOKIE = "floppy_session"
LAST_SEEN_EVERY = timedelta(minutes=5)


def _email(value: str) -> str:
    address = value.strip().lower()
    if len(address) > 254 or not EMAIL_PATTERN.fullmatch(address):
        raise ValueError("not an email address")
    return address


EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")

# An address as people type it: trimmed and lowercased, the form the schema stores. Plain
# shape check only; the mail is what proves it.
Email = Annotated[str, AfterValidator(_email)]


class CamelModel(BaseModel):
    """A request or response body: snake_case in Python, camelCase on the wire."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


@dataclass(frozen=True)
class Actor:
    """The signed-in person acting in one organization."""

    session_id: UUID
    user_id: UUID
    membership_id: UUID
    organization_id: UUID
    role: str
    email: str
    name: str | None

    @property
    def label(self) -> str:
        """The name the audit log records for this person."""
        return self.name or self.email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_context(request: Request) -> Context:
    """The process's service context."""
    ctx: Context = request.app.state.ctx
    return ctx


CtxDep = Annotated[Context, Depends(get_context)]


async def current_actor(request: Request, ctx: CtxDep) -> Actor:
    """The session's person, or 401 when the cookie is missing, expired, revoked or orphaned."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthorized()
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        row = (
            await db.execute(
                select(WebSession, Membership, User)
                .join(
                    Membership,
                    (Membership.id == WebSession.membership_id)
                    & (Membership.organization_id == WebSession.organization_id),
                )
                .join(User, User.id == WebSession.user_id)
                .join(Organization, Organization.id == WebSession.organization_id)
                .where(
                    WebSession.token_hash == token_hash(token),
                    WebSession.revoked_at.is_(None),
                    WebSession.expires_at > now,
                    WebSession.last_seen_at > now - ctx.settings.session_idle,
                    Membership.status == "active",
                    Membership.deleted_at.is_(None),
                    User.deleted_at.is_(None),
                    Organization.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise unauthorized()
        session, membership, user = row
        if now - session.last_seen_at > LAST_SEEN_EVERY:
            await db.execute(
                update(WebSession).where(WebSession.id == session.id).values(last_seen_at=now)
            )
    return Actor(
        session_id=session.id,
        user_id=user.id,
        membership_id=membership.id,
        organization_id=membership.organization_id,
        role=membership.role,
        email=user.email,
        name=user.name,
    )


async def admin_actor(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
    """The session's person, who must be an admin."""
    if not actor.is_admin:
        raise forbidden()
    return actor


ActorDep = Annotated[Actor, Depends(current_actor)]
AdminDep = Annotated[Actor, Depends(admin_actor)]


def rate_limit(rule: str) -> Callable[[Request, Context], None]:
    """A dependency counting the request against `rule` for the client's address (ratelimit.py)."""

    def check(request: Request, ctx: CtxDep) -> None:
        limits = ctx.settings.rate_limits.get(rule)
        if not limits:
            return
        address = request.client.host if request.client else "unknown"
        wait = ctx.rate_limiter.hit(rule, address, limits)
        if wait is not None:
            raise rate_limited(wait)

    return check
