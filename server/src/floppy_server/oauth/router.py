"""
The admin web's side of the authorization server: the consent (`/autorizza` in the web asks
here), and the person's connections, which they can revoke.

A request is decided by the signed-in person, from their session: the code is bound to their
membership in the session's organization. The client is resolved only after the session is
checked, because resolving it may fetch a CIMD document, an outgoing request to a URL the
caller chose.
"""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request, Response
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import Field
from sqlalchemy import func, select

from floppy_server.api import ActorDep, CamelModel, CtxDep
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import utcnow
from floppy_server.errors import ApiError, not_found
from floppy_server.models import OAuthAuthorization, OAuthToken
from floppy_server.oauth import scopes
from floppy_server.oauth.client import loopback_only
from floppy_server.oauth.provider import Provider, revoke_grant

router = APIRouter(tags=["oauth"])

Scope = Literal["floppy:read", "floppy:write", "floppy:admin"]


class ConsentClient(CamelModel):
    """Who asks: the self-declared name, its id, and where the browser will go back."""

    id: str
    name: str | None
    redirect_host: str
    loopback_only: bool


class ConsentOut(CamelModel):
    """A request waiting for the signed-in person's decision."""

    id: UUID
    client: ConsentClient
    scopes: list[Scope]
    role: Literal["admin", "member"]
    expires_at: datetime


class ApproveRequest(CamelModel):
    """The scopes the person ticked; `read` is always granted."""

    scopes: list[Scope] = Field(default_factory=list, max_length=3)


class DecisionOut(CamelModel):
    """Where the browser goes next: the client's redirect with the code, or with the refusal."""

    redirect_to: str


class ConnectionOut(CamelModel):
    """A live grant of the signed-in person: one MCP client connected as them."""

    id: UUID
    client_name: str
    client_id: str
    scopes: list[Scope]
    granted_at: datetime
    last_used_at: datetime | None


def request_expired() -> ApiError:
    """410: the request was decided, expired, or never existed."""
    return ApiError(410, "request_expired", "This authorization request is no longer open")


def _provider(request: Request) -> Provider:
    provider: Provider = request.app.state.oauth_provider
    return provider


@router.get("/oauth/requests/{request_id}", response_model=ConsentOut)
async def get_request(
    request_id: UUID, actor: ActorDep, ctx: CtxDep, request: Request
) -> ConsentOut:
    """An open request, for the consent page."""
    provider = _provider(request)
    async with ctx.sessions() as db, db.begin():
        row = await provider.pending(db, request_id)
        if row is None:
            raise request_expired()
        client_id, redirect_uri, wanted, expires_at = (
            row.client_id,
            row.redirect_uri,
            list(row.scopes),
            row.expires_at,
        )
    client = await provider.get_client(client_id)
    return ConsentOut(
        id=request_id,
        client=ConsentClient(
            id=client_id,
            name=client.client_name if client else None,
            redirect_host=urlsplit(redirect_uri).netloc or redirect_uri,
            loopback_only=bool(client and loopback_only(client)),
        ),
        scopes=wanted,
        role="admin" if actor.is_admin else "member",
        expires_at=expires_at,
    )


@router.post("/oauth/requests/{request_id}/approve", response_model=DecisionOut)
async def approve(
    request_id: UUID, body: ApproveRequest, actor: ActorDep, ctx: CtxDep, request: Request
) -> DecisionOut:
    """Approves with the ticked scopes (never more than asked) and returns the redirect."""
    provider = _provider(request)
    async with ctx.sessions() as db:
        asked = await db.get(OAuthAuthorization, request_id)
        if asked is None:
            raise request_expired()
        client_id = asked.client_id
    # Resolved before the row lock: it may mean fetching the client's document.
    client = await provider.get_client(client_id)
    async with ctx.sessions() as db, db.begin():
        row = await provider.pending(db, request_id)
        if row is None:
            raise request_expired()
        ticked = set(body.scopes) | set(scopes.REQUIRED_SCOPES)
        granted = scopes.expand(sorted(ticked & set(row.scopes)))
        code = provider.approve(
            row,
            organization_id=actor.organization_id,
            membership_id=actor.membership_id,
            user_id=actor.user_id,
            granted=granted,
        )
        record(
            db,
            actor.organization_id,
            "mcp_client_connected",
            AuditActor.person(actor.user_id, actor.label),
            Target("person", actor.user_id, actor.label),
            {
                "client": (client.client_name if client else None) or row.client_id,
                "scopes": granted,
            },
        )
        target = construct_redirect_uri(
            row.redirect_uri, code=code, state=row.state, iss=ctx.settings.issuer
        )
    return DecisionOut(redirect_to=target)


@router.post("/oauth/requests/{request_id}/deny", response_model=DecisionOut)
async def deny(request_id: UUID, actor: ActorDep, ctx: CtxDep, request: Request) -> DecisionOut:
    """Denies; the client learns it as `access_denied` (RFC 6749 #4.1.2.1)."""
    provider = _provider(request)
    async with ctx.sessions() as db, db.begin():
        row = await provider.pending(db, request_id)
        if row is None:
            raise request_expired()
        row.denied_at = utcnow()
        target = construct_redirect_uri(
            row.redirect_uri,
            error="access_denied",
            error_description="The person denied the authorization",
            state=row.state,
            iss=ctx.settings.issuer,
        )
    return DecisionOut(redirect_to=target)


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(actor: ActorDep, ctx: CtxDep) -> list[ConnectionOut]:
    """The signed-in person's connected MCP clients, newest first."""
    async with ctx.sessions() as db:
        rows = (
            await db.execute(
                select(OAuthToken)
                .where(
                    OAuthToken.organization_id == actor.organization_id,
                    OAuthToken.membership_id == actor.membership_id,
                    OAuthToken.kind == "refresh",
                    OAuthToken.revoked_at.is_(None),
                    OAuthToken.expires_at > func.now(),
                )
                .order_by(OAuthToken.granted_at.desc())
            )
        ).scalars()
        used = {
            row[0]: row[1]
            for row in (
                await db.execute(
                    select(OAuthToken.grant_id, func.max(OAuthToken.last_used_at))
                    .where(
                        OAuthToken.organization_id == actor.organization_id,
                        OAuthToken.membership_id == actor.membership_id,
                        OAuthToken.kind == "access",
                    )
                    .group_by(OAuthToken.grant_id)
                )
            ).all()
        }
        return [
            ConnectionOut(
                id=row.grant_id,
                client_name=row.client_name or row.client_id,
                client_id=row.client_id,
                scopes=row.scopes,
                granted_at=row.granted_at,
                last_used_at=used.get(row.grant_id),
            )
            for row in rows
        ]


@router.post("/connections/{grant_id}/revoke", status_code=204)
async def revoke_connection(grant_id: UUID, actor: ActorDep, ctx: CtxDep) -> Response:
    """Disconnects one of the person's clients: both its tokens stop at once."""
    async with ctx.sessions() as db, db.begin():
        owner = await db.scalar(
            select(OAuthToken.membership_id)
            .where(
                OAuthToken.organization_id == actor.organization_id,
                OAuthToken.grant_id == grant_id,
            )
            .limit(1)
        )
        if owner != actor.membership_id:
            raise not_found("No such connection")
        revoked = await revoke_grant(
            db, grant_id, "revoked", by=AuditActor.person(actor.user_id, actor.label)
        )
        if not revoked:
            raise not_found("No such connection")
    return Response(status_code=204)
