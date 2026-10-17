"""
Sign-in by magic link and 6-digit code, for the admin web and for the app; invitations
accepted from their link; web sessions.

The rules (docs/DATABASE_SCHEMA.md, "sign_in_requests" and "State machines"):

- The answer to a sign-in is the same for every address, so the page never reveals who is
  a member; the mail says "ask your admin" instead, and such a request can never complete.
- Polling never grants a session: only the link or the right code does.
- One pending request per address and kind; a new one supersedes the old. Five wrong codes
  lock the request, link included. A request lives 15 minutes.
- The mail is sent inside the transaction that creates the request or invitation: when it
  fails, nothing is left behind that points at a mail nobody received.
"""

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server import mailtext
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.crypto import new_code, new_token, pkce_challenge, token_hash
from floppy_server.errors import ApiError
from floppy_server.infra.mail import Mail, MailError
from floppy_server.models import (
    Device,
    Invitation,
    Membership,
    Organization,
    OrganizationStatus,
    SignInRequest,
    User,
    WebSession,
)

log = logging.getLogger(__name__)

SIGN_IN_TTL = timedelta(minutes=15)
RESEND_AFTER = timedelta(seconds=60)
MAX_ATTEMPTS = 5
AUTH_CODE_TTL = timedelta(seconds=60)
INVITATION_TTL = timedelta(days=7)


def request_expired() -> ApiError:
    """410: the request expired, was superseded or already used."""
    return ApiError(410, "request_expired", "The sign-in request is no longer valid")


def request_locked() -> ApiError:
    """429: five wrong codes locked the request."""
    return ApiError(429, "request_locked", "Too many wrong codes")


def link_invalid() -> ApiError:
    """410: the link expired, was used, or never belonged to a member."""
    return ApiError(410, "link_invalid", "The link expired or was already used")


def mail_failed(error: MailError) -> ApiError:
    """503: the mail could not be sent; nothing was created."""
    return ApiError(503, "mail_failed", "The mail could not be sent", {"reason": str(error)[:200]})


@dataclass(frozen=True)
class Member:
    """A user's membership that may sign in, with its organization."""

    user: User
    membership: Membership
    organization: Organization


@dataclass(frozen=True)
class OpenedSession:
    """A web session just opened: the cookie's token and who it belongs to."""

    token: str
    session: WebSession
    member: Member


@dataclass(frozen=True)
class DeviceGrant:
    """A device created by the app's token exchange: the token is returned once."""

    device_token: str
    device: Device


def name_from_email(email: str) -> str:
    """A first name from an address, until the person says otherwise: giulia@x becomes Giulia."""
    local = email.split("@", 1)[0]
    first = local.replace("_", ".").replace("-", ".").split(".")[0] or local
    return first[:1].upper() + first[1:100]


async def eligible_member(db: AsyncSession, user_id: UUID, now: datetime) -> Member | None:
    """
    The membership a user signs in with: active, or invited with a live invitation (an
    invitation past its 7 days needs a resend first). Removed and soft-deleted rows never.
    """
    live_invitation = (
        select(Invitation.id)
        .where(
            Invitation.membership_id == Membership.id,
            Invitation.organization_id == Membership.organization_id,
            Invitation.state == "sent",
            Invitation.expires_at > now,
        )
        .exists()
    )
    row = (
        await db.execute(
            select(User, Membership, Organization)
            .join(Membership, Membership.user_id == User.id)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(
                User.id == user_id,
                User.deleted_at.is_(None),
                Membership.deleted_at.is_(None),
                Organization.deleted_at.is_(None),
                (Membership.status == "active")
                | ((Membership.status == "invited") & live_invitation),
            )
            .order_by(Membership.created_at)
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return None
    user, membership, organization = row
    return Member(user=user, membership=membership, organization=organization)


async def _user_by_email(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
    ).scalar_one_or_none()


async def send_mail(ctx: Context, db: AsyncSession, mail: Mail, org: Organization | None) -> None:
    """Sends inside the caller's transaction and keeps the organization's mail state current."""
    try:
        await ctx.mailer.send(mail, ctx.smtp_for(org))
    except MailError as error:
        log.warning("mail to an address failed: %s", type(error).__name__)
        if org is not None:
            await mark_mail_state(ctx, org.id, ok=False)
        raise mail_failed(error) from error
    if org is not None:
        await db.execute(
            update(OrganizationStatus)
            .where(OrganizationStatus.organization_id == org.id)
            .values(mail_state="ok", mail_sent_at=utcnow())
        )


async def mark_mail_state(ctx: Context, organization_id: UUID, *, ok: bool) -> None:
    """Records a mail failure in its own transaction, so it survives the caller's rollback."""
    async with ctx.sessions() as db, db.begin():
        await db.execute(
            update(OrganizationStatus)
            .where(OrganizationStatus.organization_id == organization_id)
            .values(mail_state="ok" if ok else "failing")
        )


async def start_sign_in(
    ctx: Context,
    *,
    kind: str,
    email: str,
    code_challenge: str | None = None,
    redirect_uri: str | None = None,
    device_name: str | None = None,
    accept_language: str | None = None,
) -> SignInRequest:
    """A new sign-in request with its mail, or the current one while it cannot be resent yet."""
    address = email.strip().lower()
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        recent = (
            (
                await db.execute(
                    select(SignInRequest)
                    .where(
                        SignInRequest.email == address,
                        SignInRequest.created_at > now - timedelta(hours=1),
                    )
                    .order_by(SignInRequest.created_at.desc())
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        same_kind = [r for r in recent if r.kind == kind]
        pending = next(
            (r for r in same_kind if r.state == "pending" and r.expires_at > now),
            None,
        )
        if kind == "app" and code_challenge is not None:
            clash = (
                await db.execute(
                    select(SignInRequest).where(SignInRequest.code_challenge == code_challenge)
                )
            ).scalar_one_or_none()
            if clash is not None:
                if clash.email == address and clash.state == "pending" and clash.expires_at > now:
                    return clash
                raise ApiError(422, "validation", "Use a new PKCE verifier for every sign-in")
        # Too early to resend, or too many this hour: the same answer, no new mail.
        if pending is not None and pending.resend_at > now:
            return pending
        if len(recent) >= ctx.settings.sign_in_hourly_limit and same_kind:
            return same_kind[0]

        user = await _user_by_email(db, address)
        member = await eligible_member(db, user.id, now) if user is not None else None
        await db.execute(
            update(SignInRequest)
            .where(
                SignInRequest.email == address,
                SignInRequest.kind == kind,
                SignInRequest.state == "pending",
            )
            .values(state="superseded", superseded_at=now)
        )
        link_token, code = new_token(), new_code()
        request = SignInRequest(
            kind=kind,
            email=address,
            user_id=member.user.id if member else None,
            organization_id=member.organization.id if member else None,
            link_token_hash=token_hash(link_token),
            code_hmac=b"",
            expires_at=now + SIGN_IN_TTL,
            resend_at=now + RESEND_AFTER,
            device_name=device_name,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            created_at=now,
        )
        db.add(request)
        await db.flush()
        request.code_hmac = ctx.box.code_hmac(request.id, code)
        org = member.organization if member else None
        lang = (
            mail_language(org)
            if org
            else mailtext.language_of(accept_language, ctx.settings.default_mail_language)
        )
        if member is None:
            mail = mailtext.not_a_member(lang=lang, to=address)
        elif kind == "app":
            mail = mailtext.app_sign_in(
                lang=lang,
                to=address,
                organization=org.name if org else None,
                public_url=ctx.settings.public_url,
                token=link_token,
                code=code,
                device=device_name or "Mac",
            )
        else:
            mail = mailtext.sign_in(
                lang=lang,
                to=address,
                organization=org.name if org else None,
                public_url=ctx.settings.public_url,
                token=link_token,
                code=code,
            )
        await send_mail(ctx, db, mail, org)
        return request


def mail_language(org: Organization) -> mailtext.Language:
    """The language of the mail sent for an organization."""
    return "en" if org.mail_language == "en" else "it"


def status_of(request: SignInRequest, now: datetime) -> str:
    """The API's status of a request: superseded and past-expiry both read as expired."""
    if request.state == "completed":
        return "completed"
    if request.state == "locked":
        return "locked"
    if request.state == "superseded" or request.expires_at <= now:
        return "expired"
    return "pending"


async def sign_in_status(ctx: Context, request_id: UUID) -> str | None:
    """The status of a request, or None when there is no such request."""
    async with ctx.sessions() as db:
        request = await db.get(SignInRequest, request_id)
    return status_of(request, utcnow()) if request is not None else None


async def _activate(
    db: AsyncSession, member: Member, now: datetime, *, invitation: Invitation | None = None
) -> None:
    """First sign-in effects: name from the address, membership active, invitation accepted."""
    user, membership, org = member.user, member.membership, member.organization
    if user.name is None:
        user.name = name_from_email(user.email)
    user.last_sign_in_at = now
    if membership.status == "invited":
        membership.status = "active"
        membership.activated_at = now
        if invitation is None:
            invitation = (
                await db.execute(
                    select(Invitation)
                    .where(
                        Invitation.organization_id == org.id,
                        Invitation.membership_id == membership.id,
                        Invitation.state == "sent",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
        if invitation is not None:
            invitation.state = "accepted"
            invitation.accepted_at = now
        record(
            db,
            org.id,
            "invite_accepted",
            AuditActor.person(user.id, user.name or user.email),
            Target("person", user.id, user.name or user.email),
        )


def _signed_in(db: AsyncSession, member: Member, *, via: str, kind: str) -> None:
    user = member.user
    label = user.name or user.email
    record(
        db,
        member.organization.id,
        "signed_in",
        AuditActor.person(user.id, label),
        Target("person", user.id, label),
        {"via": via, "kind": kind},
    )


async def _open_session(
    ctx: Context,
    db: AsyncSession,
    member: Member,
    now: datetime,
    *,
    user_agent: str | None,
    request: SignInRequest | None = None,
    invitation: Invitation | None = None,
) -> OpenedSession:
    token = new_token()
    session = WebSession(
        organization_id=member.organization.id,
        membership_id=member.membership.id,
        user_id=member.user.id,
        sign_in_request_id=request.id if request else None,
        invitation_id=invitation.id if invitation else None,
        token_hash=token_hash(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + ctx.settings.session_absolute,
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(session)
    await db.flush()
    return OpenedSession(token=token, session=session, member=member)


async def _complete(
    db: AsyncSession, request: SignInRequest, now: datetime, via: str
) -> Member | None:
    """Marks a pending request completed for an eligible member; None when it cannot complete."""
    if request.user_id is None:
        return None
    member = await eligible_member(db, request.user_id, now)
    if member is None or member.organization.id != request.organization_id:
        return None
    request.state = "completed"
    request.completed_at = now
    request.completed_via = via
    await _activate(db, member, now)
    _signed_in(db, member, via=via, kind=request.kind)
    return member


async def _wrong_code(db: AsyncSession, request: SignInRequest, now: datetime) -> ApiError:
    """Counts a wrong code; the fifth locks the request. Returns the error to raise after commit."""
    request.code_attempts += 1
    if request.code_attempts >= MAX_ATTEMPTS:
        request.state = "locked"
        request.locked_at = now
        if request.user_id is not None and request.organization_id is not None:
            user = await db.get(User, request.user_id)
            label = (user.name or user.email) if user else request.email
            record(
                db,
                request.organization_id,
                "sign_in_locked",
                AuditActor.system(),
                Target("person", request.user_id, label),
            )
        return request_locked()
    return ApiError(
        422, "invalid_code", "Wrong code", {"attemptsLeft": MAX_ATTEMPTS - request.code_attempts}
    )


def _code_matches(ctx: Context, request: SignInRequest, code: str) -> bool:
    digits = "".join(c for c in code if c.isdigit())
    return len(digits) == 6 and hmac.compare_digest(
        ctx.box.code_hmac(request.id, digits), request.code_hmac
    )


def _check_code_usable(request: SignInRequest | None, now: datetime, kind: str) -> SignInRequest:
    if request is None or request.kind != kind:
        raise request_expired()
    if request.state == "locked":
        raise request_locked()
    if request.state != "pending" or request.expires_at <= now:
        raise request_expired()
    return request


async def verify_code(
    ctx: Context, request_id: UUID, code: str, *, user_agent: str | None
) -> OpenedSession:
    """Opens a web session with the 6-digit code, counting wrong attempts."""
    now = utcnow()
    error: ApiError | None = None
    async with ctx.sessions() as db, db.begin():
        request = _check_code_usable(
            (
                await db.execute(
                    select(SignInRequest).where(SignInRequest.id == request_id).with_for_update()
                )
            ).scalar_one_or_none(),
            now,
            "web",
        )
        member = (
            await _complete(db, request, now, "code") if _code_matches(ctx, request, code) else None
        )
        if member is None:
            error = await _wrong_code(db, request, now)
        else:
            return await _open_session(ctx, db, member, now, user_agent=user_agent, request=request)
    raise error


async def open_link(ctx: Context, link: str, *, user_agent: str | None) -> OpenedSession:
    """Opens a web session with the magic link's token."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        request = (
            await db.execute(
                select(SignInRequest)
                .where(
                    SignInRequest.link_token_hash == token_hash(link), SignInRequest.kind == "web"
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request is None or request.state != "pending" or request.expires_at <= now:
            raise link_invalid()
        member = await _complete(db, request, now, "link")
        if member is None:
            raise link_invalid()
        return await _open_session(ctx, db, member, now, user_agent=user_agent, request=request)


async def accept_invitation(ctx: Context, link: str, *, user_agent: str | None) -> OpenedSession:
    """Accepts an invitation from its link and opens a web session: the mail proves the address."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        invitation = (
            await db.execute(
                select(Invitation)
                .where(Invitation.token_hash == token_hash(link))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if invitation is None or invitation.state != "sent" or invitation.expires_at <= now:
            raise link_invalid()
        membership = (
            await db.execute(
                select(Membership)
                .where(
                    Membership.id == invitation.membership_id,
                    Membership.organization_id == invitation.organization_id,
                    Membership.status == "invited",
                    Membership.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if membership is None:
            raise link_invalid()
        user = await db.get(User, membership.user_id)
        org = await db.get(Organization, membership.organization_id)
        if user is None or user.deleted_at is not None or org is None or org.deleted_at is not None:
            raise link_invalid()
        member = Member(user=user, membership=membership, organization=org)
        await _activate(db, member, now, invitation=invitation)
        _signed_in(db, member, via="invitation", kind="web")
        return await _open_session(
            ctx, db, member, now, user_agent=user_agent, invitation=invitation
        )


async def sign_out(ctx: Context, session_token: str | None) -> None:
    """Revokes the cookie's session, if it is still live."""
    if not session_token:
        return
    async with ctx.sessions() as db, db.begin():
        await db.execute(
            update(WebSession)
            .where(
                WebSession.token_hash == token_hash(session_token), WebSession.revoked_at.is_(None)
            )
            .values(revoked_at=utcnow(), revoke_reason="signed_out")
        )


# The app's sign-in (PKCE with a loopback redirect).


def loopback_redirect(redirect_uri: str, auth_code: str) -> str:
    """The loopback address with the one-time authorization code added to its query."""
    parts = urlsplit(redirect_uri)
    query = f"{parts.query}&" if parts.query else ""
    return urlunsplit(parts._replace(query=query + urlencode({"code": auth_code})))


async def open_app_link(ctx: Context, link: str) -> str:
    """Completes an app request from its link and returns where to send the browser."""
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        request = (
            await db.execute(
                select(SignInRequest)
                .where(
                    SignInRequest.link_token_hash == token_hash(link), SignInRequest.kind == "app"
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request is None or request.state != "pending" or request.expires_at <= now:
            raise link_invalid()
        member = await _complete(db, request, now, "link")
        if member is None or request.redirect_uri is None:
            raise link_invalid()
        auth_code = new_token()
        request.auth_code_hash = token_hash(auth_code)
        request.auth_code_expires_at = now + AUTH_CODE_TTL
        return loopback_redirect(request.redirect_uri, auth_code)


async def exchange_app_token(
    ctx: Context, *, code_verifier: str, auth_code: str | None, code: str | None
) -> DeviceGrant:
    """
    Trades the PKCE verifier plus the authorization code (after the link) or the 6-digit
    code (typed in the app) for a device token. Single use: the request is exchanged once.
    """
    now = utcnow()
    error: ApiError | None = None
    async with ctx.sessions() as db, db.begin():
        request = (
            await db.execute(
                select(SignInRequest)
                .where(
                    SignInRequest.code_challenge == pkce_challenge(code_verifier),
                    SignInRequest.kind == "app",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        member: Member | None
        if auth_code is not None:
            if (
                request is None
                or request.state != "completed"
                or request.exchanged_at is not None
                or request.auth_code_hash is None
                or request.auth_code_hash != token_hash(auth_code)
                or request.auth_code_expires_at is None
                or request.auth_code_expires_at <= now
            ):
                raise link_invalid()
            member = await eligible_member(db, request.user_id, now) if request.user_id else None
            if member is None:
                raise link_invalid()
        else:
            usable = _check_code_usable(request, now, "app")
            member = (
                await _complete(db, usable, now, "code")
                if _code_matches(ctx, usable, code or "")
                else None
            )
            if member is None:
                error = await _wrong_code(db, usable, now)
        if error is None and request is not None and member is not None:
            request.exchanged_at = now
            device_token = new_token()
            device = Device(
                organization_id=member.organization.id,
                membership_id=member.membership.id,
                user_id=member.user.id,
                name=(request.device_name or "Mac")[:100],
                sign_in_request_id=request.id,
                token_hash=token_hash(device_token),
            )
            db.add(device)
            await db.flush()
            await db.refresh(device)
            return DeviceGrant(device_token=device_token, device=device)
    raise error or request_expired()
