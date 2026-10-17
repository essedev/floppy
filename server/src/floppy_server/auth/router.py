"""
Sign-in, invitations and the session, for the admin web; the app's sign-in under
`/auth/app/*`. The session lives in an HttpOnly cookie set here and nowhere else.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.api import ActorDep, CamelModel, CtxDep, Email, rate_limit
from floppy_server.auth import app_page, service
from floppy_server.context import Context
from floppy_server.errors import ApiError, not_found, validation
from floppy_server.models import Device, Membership, Organization, SignInRequest, User, Volume
from floppy_server.paths import API_PREFIX

router = APIRouter(tags=["auth"])


class SignInStartRequest(CamelModel):
    """Asks for a magic link and a 6-digit code by mail."""

    email: Email


class SignInStartResponse(CamelModel):
    """The pending request; the same answer whether or not the address belongs to a team."""

    request_id: UUID
    email: str
    expires_at: datetime
    resend_at: datetime


class SignInStatusResponse(CamelModel):
    """The state the waiting page polls."""

    status: str


class CodeRequest(CamelModel):
    """The 6-digit code typed on the waiting page."""

    code: str = Field(min_length=1, max_length=20)


class TokenRequest(CamelModel):
    """A token from a link in a mail."""

    token: str = Field(min_length=10, max_length=200)


class SessionUser(CamelModel):
    """The signed-in person."""

    id: UUID
    email: str
    name: str


class OrganizationSummary(CamelModel):
    """The organization with the counts the sidebar shows."""

    id: UUID
    name: str
    disk_count: int
    people_count: int
    device_count: int


class SessionOut(CamelModel):
    """The signed-in person, their role and their organization."""

    user: SessionUser
    role: str
    organization: OrganizationSummary


class AppSignInRequest(CamelModel):
    """The app's sign-in: the address, its PKCE challenge, loopback and the device's name."""

    email: Email
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    redirect_uri: str = Field(pattern=r"^http://127\.0\.0\.1:[0-9]{1,5}/", max_length=200)
    device_name: str = Field(min_length=1, max_length=100)


class AppLinkResponse(CamelModel):
    """Where the browser goes after the app's link: the app's loopback, with a one-time code."""

    redirect_to: str


class AppTokenRequest(CamelModel):
    """The PKCE verifier with either the authorization code (after the link) or the 6-digit code."""

    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")
    authorization_code: str | None = Field(default=None, max_length=200)
    code: str | None = Field(default=None, max_length=20)


class AppTokenResponse(CamelModel):
    """The new device, `pending` until it registers, and its token (returned once, for the Keychain)."""

    device_id: UUID
    device_token: str


async def session_view(
    db: AsyncSession, user: User, membership: Membership, org: Organization
) -> SessionOut:
    """The `Session` shape for a person in an organization."""
    disks = await db.scalar(
        select(func.count()).where(Volume.organization_id == org.id, Volume.deleted_at.is_(None))
    )
    people = await db.scalar(
        select(func.count()).where(
            Membership.organization_id == org.id,
            Membership.status != "removed",
            Membership.deleted_at.is_(None),
        )
    )
    devices_query = select(func.count()).where(
        Device.organization_id == org.id, Device.state != "revoked", Device.deleted_at.is_(None)
    )
    if membership.role != "admin":
        devices_query = devices_query.where(Device.user_id == user.id)
    devices = await db.scalar(devices_query)
    return SessionOut(
        user=SessionUser(id=user.id, email=user.email, name=user.name or user.email),
        role=membership.role,
        organization=OrganizationSummary(
            id=org.id,
            name=org.name,
            disk_count=disks or 0,
            people_count=people or 0,
            device_count=devices or 0,
        ),
    )


def _set_cookie(ctx: Context, response: Response, token: str) -> None:
    response.set_cookie(
        ctx.settings.session_cookie,
        token,
        max_age=int(ctx.settings.session_absolute.total_seconds()),
        httponly=True,
        secure=ctx.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def _opened(ctx: Context, response: Response, opened: service.OpenedSession) -> SessionOut:
    _set_cookie(ctx, response, opened.token)
    member = opened.member
    async with ctx.sessions() as db:
        return await session_view(db, member.user, member.membership, member.organization)


def _start_response(request: SignInRequest) -> SignInStartResponse:
    return SignInStartResponse(
        request_id=request.id,
        email=request.email,
        expires_at=request.expires_at,
        resend_at=request.resend_at,
    )


@router.post(
    "/auth/sign-in",
    response_model=SignInStartResponse,
    dependencies=[Depends(rate_limit("sign-in"))],
)
async def start_sign_in(
    body: SignInStartRequest, request: Request, ctx: CtxDep
) -> SignInStartResponse:
    """Sends a magic link and a code."""
    started = await service.start_sign_in(
        ctx, kind="web", email=body.email, accept_language=request.headers.get("accept-language")
    )
    return _start_response(started)


@router.get("/auth/sign-in/{request_id}", response_model=SignInStatusResponse)
async def sign_in_status(request_id: UUID, ctx: CtxDep) -> SignInStatusResponse:
    """The request's state; never grants a session."""
    status = await service.sign_in_status(ctx, request_id)
    if status is None:
        raise not_found("No such request")
    return SignInStatusResponse(status=status)


@router.post(
    "/auth/sign-in/{request_id}/code",
    response_model=SessionOut,
    dependencies=[Depends(rate_limit("code"))],
)
async def verify_code(
    request_id: UUID, body: CodeRequest, request: Request, response: Response, ctx: CtxDep
) -> SessionOut:
    """Signs in with the 6-digit code."""
    opened = await service.verify_code(
        ctx, request_id, body.code, user_agent=request.headers.get("user-agent")
    )
    return await _opened(ctx, response, opened)


@router.post(
    "/auth/magic-link", response_model=SessionOut, dependencies=[Depends(rate_limit("link"))]
)
async def magic_link(
    body: TokenRequest, request: Request, response: Response, ctx: CtxDep
) -> SessionOut:
    """Signs in with the link's token."""
    opened = await service.open_link(ctx, body.token, user_agent=request.headers.get("user-agent"))
    return await _opened(ctx, response, opened)


@router.post(
    "/auth/invitation", response_model=SessionOut, dependencies=[Depends(rate_limit("link"))]
)
async def accept_invitation(
    body: TokenRequest, request: Request, response: Response, ctx: CtxDep
) -> SessionOut:
    """Accepts an invitation from its link and signs in."""
    opened = await service.accept_invitation(
        ctx, body.token, user_agent=request.headers.get("user-agent")
    )
    return await _opened(ctx, response, opened)


@router.get("/session", response_model=SessionOut)
async def get_session(actor: ActorDep, ctx: CtxDep) -> SessionOut:
    """The current session; 401 when signed out."""
    async with ctx.sessions() as db:
        user = await db.get(User, actor.user_id)
        membership = await db.get(Membership, actor.membership_id)
        org = await db.get(Organization, actor.organization_id)
        if user is None or membership is None or org is None:
            raise not_found()
        return await session_view(db, user, membership, org)


@router.delete("/session", status_code=204)
async def delete_session(request: Request, response: Response, ctx: CtxDep) -> Response:
    """Signs out: the session is revoked and the cookie cleared."""
    name = ctx.settings.session_cookie
    await service.sign_out(ctx, request.cookies.get(name))
    response.status_code = 204
    response.delete_cookie(
        name, path="/", httponly=True, secure=ctx.settings.cookie_secure, samesite="lax"
    )
    return response


@router.post(
    "/auth/app/sign-in",
    response_model=SignInStartResponse,
    dependencies=[Depends(rate_limit("sign-in"))],
)
async def start_app_sign_in(
    body: AppSignInRequest, request: Request, ctx: CtxDep
) -> SignInStartResponse:
    """The app's sign-in: a magic link that ends on the app's loopback, and a code."""
    started = await service.start_sign_in(
        ctx,
        kind="app",
        email=body.email,
        code_challenge=body.code_challenge,
        redirect_uri=body.redirect_uri,
        device_name=body.device_name.strip(),
        accept_language=request.headers.get("accept-language"),
    )
    return _start_response(started)


@router.get("/auth/app/link", response_class=HTMLResponse, include_in_schema=False)
async def app_link_page(token: Annotated[str, Query(max_length=200)] = "") -> HTMLResponse:
    """The page the app's mail links to: a button, so opening the link changes nothing."""
    return HTMLResponse(
        app_page.confirm(token, f"{API_PREFIX}/auth/app/link/continue"),
        headers={"Content-Security-Policy": app_page.CONTENT_SECURITY_POLICY},
    )


@router.post(
    "/auth/app/link/continue",
    include_in_schema=False,
    response_model=None,
    dependencies=[Depends(rate_limit("link"))],
)
async def app_link_continue(
    token: Annotated[str, Form(max_length=200)], ctx: CtxDep
) -> RedirectResponse | HTMLResponse:
    """The page's button: completes the request and sends the browser to the app's loopback."""
    headers = {"Content-Security-Policy": app_page.CONTENT_SECURITY_POLICY}
    try:
        target = await service.open_app_link(ctx, token)
    except ApiError as error:
        if error.code != "link_invalid":
            raise
        return HTMLResponse(app_page.invalid(), status_code=410, headers=headers)
    return RedirectResponse(target, status_code=303)


@router.post(
    "/auth/app/link", response_model=AppLinkResponse, dependencies=[Depends(rate_limit("link"))]
)
async def open_app_link(body: TokenRequest, ctx: CtxDep) -> AppLinkResponse:
    """Completes an app request from its link; the web page then sends the browser to the loopback."""
    return AppLinkResponse(redirect_to=await service.open_app_link(ctx, body.token))


@router.post(
    "/auth/app/token", response_model=AppTokenResponse, dependencies=[Depends(rate_limit("code"))]
)
async def app_token(body: AppTokenRequest, ctx: CtxDep) -> AppTokenResponse:
    """Trades the verifier and a code for a device token (the device is created, pending)."""
    if (body.authorization_code is None) == (body.code is None):
        raise validation("Send either authorizationCode or code")
    grant = await service.exchange_app_token(
        ctx, code_verifier=body.code_verifier, auth_code=body.authorization_code, code=body.code
    )
    return AppTokenResponse(device_id=grant.device.id, device_token=grant.device_token)
