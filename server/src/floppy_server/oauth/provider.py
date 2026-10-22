"""
The authorization server's grants on PostgreSQL: the SDK's `OAuthAuthorizationServerProvider`.

The SDK validates `redirect_uri`, `scope` and PKCE; this module keeps what was granted, to
whom, for which resource and until when. What it adds and the spec asks for:

- `authorize` issues no code: it opens a request and sends the browser to the consent in the
  admin web, where a signed-in person approves (`approve`) or denies (`deny`);
- the code is spent once; a replayed code revokes everything it produced (RFC 6749 #10.5);
- a refresh rotates both tokens and keeps the `grant_id`, which is the connection the person
  sees and revokes in the admin web;
- a token acts as one membership, and only while it is live: a removed person's tokens stop
  at once, like their web sessions.

Codes and tokens are stored as SHA-256 (`crypto.token_hash`), like every other token here.
"""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.context import Context, utcnow
from floppy_server.crypto import new_token, token_hash
from floppy_server.models import (
    Membership,
    OAuthAuthorization,
    OAuthClient,
    Organization,
    User,
)
from floppy_server.models import OAuthToken as TokenRow
from floppy_server.oauth import cimd, scopes
from floppy_server.oauth.client import FloppyClient

log = logging.getLogger(__name__)

CONSENT_PATH = "/autorizza"
# How often a token's `last_used_at` is written: the connections list needs the day, not the second.
LAST_USED_EVERY = timedelta(minutes=5)


async def live_member(
    db: AsyncSession, organization_id: UUID, membership_id: UUID
) -> tuple[Membership, User] | None:
    """The active membership and its user, or None when removed, deleted or in a deleted organization."""
    row = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(
                Membership.organization_id == organization_id,
                Membership.id == membership_id,
                Membership.status == "active",
                Membership.deleted_at.is_(None),
                User.deleted_at.is_(None),
                Organization.deleted_at.is_(None),
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


async def revoke_grant(
    db: AsyncSession,
    grant_id: UUID,
    reason: str,
    *,
    by: AuditActor | None = None,
    who: str = "person",
) -> bool:
    """
    Revokes both halves of a grant and records the disconnection when `by` is given; whether
    anything was live.
    """
    rows = list(
        (
            await db.execute(
                select(TokenRow)
                .where(TokenRow.grant_id == grant_id, TokenRow.revoked_at.is_(None))
                .with_for_update()
            )
        ).scalars()
    )
    if not rows:
        return False
    now = utcnow()
    for row in rows:
        row.revoked_at = now
        row.revoke_reason = reason
    if by is not None:
        first = rows[0]
        user = await db.get(User, first.user_id)
        record(
            db,
            first.organization_id,
            "mcp_client_disconnected",
            by,
            Target("person", first.user_id, (user.name or user.email) if user else ""),
            {"client": first.client_name or first.client_id, "by": who},
        )
    return True


def _bound(authorization: OAuthAuthorization) -> tuple[UUID, UUID, UUID]:
    """The organization, membership and user an approved request is bound to."""
    if (
        authorization.organization_id is None
        or authorization.membership_id is None
        or authorization.user_id is None
    ):
        raise TokenError(error="invalid_grant", error_description="Not approved")
    return authorization.organization_id, authorization.membership_id, authorization.user_id


def _registered_scopes(client: OAuthClientInformationFull) -> list[str]:
    return (client.scope or "").split() or list(scopes.ADVERTISED_SCOPES)


class Provider:
    """The authorization server of this control plane; state lives in the database only."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    # Clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """A client from the table, or from its CIMD document; a stale copy beats none."""
        async with self.ctx.sessions() as db, db.begin():
            row = await self._client_row(db, client_id)
            fresh = row is not None and (row.fresh_until is None or row.fresh_until > utcnow())
            if row is not None and fresh:
                return FloppyClient.model_validate(row.info)
            if not cimd.is_document_url(client_id):
                return None
        fetched = await cimd.fetch(self.ctx.settings, client_id)
        if fetched is None:
            if row is not None:
                log.warning("cimd document unreachable, reusing the stale copy: %s", client_id)
                return FloppyClient.model_validate(row.info)
            return None
        client, fresh_until = fetched
        async with self.ctx.sessions() as db, db.begin():
            await self._upsert(db, client, source="cimd", fresh_until=fresh_until)
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Dynamic registration, kept for clients without CIMD; the SDK generates the id."""
        async with self.ctx.sessions() as db, db.begin():
            await self._upsert(db, client_info, source="dcr", fresh_until=None)

    # Authorization

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Opens a request and returns the consent page's address; no code yet."""
        requested = scopes.expand(list(params.scopes or _registered_scopes(client)))
        async with self.ctx.sessions() as db, db.begin():
            request = OAuthAuthorization(
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_explicit=params.redirect_uri_provided_explicitly,
                scopes=requested or list(scopes.REQUIRED_SCOPES),
                code_challenge=params.code_challenge,
                state=params.state,
                resource=params.resource,
                expires_at=utcnow() + timedelta(minutes=self.ctx.settings.oauth_request_minutes),
            )
            db.add(request)
            await db.flush()
            request_id = request.id
        return (
            f"{self.ctx.settings.issuer}{CONSENT_PATH}?{urlencode({'richiesta': str(request_id)})}"
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        """The code when it is this client's, unexpired and unspent; a replay revokes its grants."""
        async with self.ctx.sessions() as db, db.begin():
            row = await self._by_code(db, authorization_code)
            if row is None or row.client_id != client.client_id:
                return None
            if row.consumed_at is not None:
                log.warning("authorization code replayed by %s", client.client_id)
                await self._revoke_for_authorization(db, row.id)
                return None
            if row.expires_at <= utcnow():
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=list(row.scopes),
                expires_at=row.expires_at.timestamp(),
                client_id=row.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=row.redirect_uri,
                redirect_uri_provided_explicitly=row.redirect_uri_explicit,
                resource=row.resource,
                subject=str(row.membership_id),
            )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Spends the code and issues the grant, while the person who approved is still a member."""
        replayed = False
        async with self.ctx.sessions() as db, db.begin():
            row = await self._by_code(db, authorization_code.code, lock=True)
            if row is None or row.client_id != client.client_id or row.expires_at <= utcnow():
                raise TokenError(error="invalid_grant", error_description="Invalid code")
            if row.consumed_at is not None:
                # Committed before the error: the revocation must survive the refusal.
                await self._revoke_for_authorization(db, row.id)
                replayed = True
        if replayed:
            raise TokenError(error="invalid_grant", error_description="Code already used")
        async with self.ctx.sessions() as db, db.begin():
            row = await self._by_code(db, authorization_code.code, lock=True)
            if row is None or row.consumed_at is not None:
                raise TokenError(error="invalid_grant", error_description="Code already used")
            row.consumed_at = utcnow()
            organization_id, membership_id, _ = _bound(row)
            member = await live_member(db, organization_id, membership_id)
            if member is None:
                raise TokenError(error="invalid_grant", error_description="No longer a member")
            return await self._issue(
                db,
                grant_id=uuid4(),
                granted_at=row.approved_at or utcnow(),
                authorization=row,
                client=client,
                granted=list(row.scopes),
            )

    # Refresh

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """A live refresh token of this client, of a live member."""
        async with self.ctx.sessions() as db, db.begin():
            row = await self._live_token(db, refresh_token, "refresh")
            if row is None or row.client_id != client.client_id:
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=list(row.scopes),
                expires_at=int(row.expires_at.timestamp()),
                resource=row.resource,
                subject=str(row.membership_id),
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes_requested: list[str],
    ) -> OAuthToken:
        """Rotates both tokens under the same grant; the scopes can only narrow."""
        async with self.ctx.sessions() as db, db.begin():
            row = await self._live_token(db, refresh_token.token, "refresh", lock=True)
            if row is None or row.client_id != client.client_id:
                raise TokenError(error="invalid_grant", error_description="Invalid refresh token")
            requested = set(scopes_requested or row.scopes)
            if not requested <= set(row.scopes):
                raise TokenError(
                    error="invalid_scope", error_description="A refresh cannot widen the scopes"
                )
            authorization = await db.get(OAuthAuthorization, row.authorization_id)
            if authorization is None:
                raise TokenError(error="invalid_grant", error_description="Invalid refresh token")
            await revoke_grant(db, row.grant_id, "refreshed")
            return await self._issue(
                db,
                grant_id=row.grant_id,
                granted_at=row.granted_at,
                authorization=authorization,
                client=client,
                granted=scopes.expand(sorted(requested)),
            )

    # Verification and revocation

    async def load_access_token(self, token: str) -> AccessToken | None:
        """A live access token of a live member, with the grant in `claims`."""
        async with self.ctx.sessions() as db, db.begin():
            row = await self._live_token(db, token, "access")
            if row is None:
                return None
            now = utcnow()
            if row.last_used_at is None or now - row.last_used_at > LAST_USED_EVERY:
                await db.execute(
                    update(TokenRow).where(TokenRow.id == row.id).values(last_used_at=now)
                )
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=list(row.scopes),
                expires_at=int(row.expires_at.timestamp()),
                resource=row.resource,
                subject=str(row.membership_id),
                claims={
                    "iss": self.ctx.settings.issuer,
                    "grant_id": str(row.grant_id),
                    "organization_id": str(row.organization_id),
                },
            )

    async def verify_token(self, token: str) -> AccessToken | None:
        """The SDK's `TokenVerifier`: the same as loading the access token."""
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revokes the whole grant, whichever half the client sent (RFC 7009 #2.1)."""
        kind = "access" if isinstance(token, AccessToken) else "refresh"
        async with self.ctx.sessions() as db, db.begin():
            row = (
                await db.execute(
                    select(TokenRow).where(
                        TokenRow.token_hash == token_hash(token.token), TokenRow.kind == kind
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return
            user = await db.get(User, row.user_id)
            label = (user.name or user.email) if user else ""
            await revoke_grant(
                db, row.grant_id, "revoked", by=AuditActor.person(row.user_id, label), who="client"
            )

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: object
    ) -> OAuthToken:
        """SEP-990 identity assertions are not accepted: people sign in by mail, here."""
        raise TokenError(
            error="unsupported_grant_type",
            error_description="This authorization server does not accept identity assertions",
        )

    # Consent (the admin web's side, oauth/router.py)

    async def pending(self, db: AsyncSession, request_id: UUID) -> OAuthAuthorization | None:
        """A request still waiting for a decision, locked; None when decided or expired."""
        row = (
            await db.execute(
                select(OAuthAuthorization)
                .where(OAuthAuthorization.id == request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.approved_at is not None or row.denied_at is not None:
            return None
        if row.expires_at <= utcnow():
            return None
        return row

    def approve(
        self,
        request: OAuthAuthorization,
        *,
        organization_id: UUID,
        membership_id: UUID,
        user_id: UUID,
        granted: list[str],
    ) -> str:
        """Binds the request to the person who approved and returns the new code."""
        code = new_token()
        request.organization_id = organization_id
        request.membership_id = membership_id
        request.user_id = user_id
        request.scopes = granted
        request.code_hash = token_hash(code)
        request.approved_at = utcnow()
        return code

    # Internals

    async def _client_row(self, db: AsyncSession, client_id: str) -> OAuthClient | None:
        return (
            await db.execute(
                select(OAuthClient).where(
                    OAuthClient.client_id == client_id, OAuthClient.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()

    async def _upsert(
        self,
        db: AsyncSession,
        client: OAuthClientInformationFull,
        *,
        source: str,
        fresh_until: datetime | None,
    ) -> None:
        info = client.model_dump(mode="json", exclude_none=True)
        row = await self._client_row(db, client.client_id)
        if row is None:
            db.add(
                OAuthClient(
                    client_id=client.client_id,
                    source=source,
                    client_name=client.client_name,
                    info=info,
                    fresh_until=fresh_until,
                )
            )
            return
        row.source = source
        row.client_name = client.client_name
        row.info = info
        row.fresh_until = fresh_until

    async def _by_code(
        self, db: AsyncSession, code: str, *, lock: bool = False
    ) -> OAuthAuthorization | None:
        query = select(OAuthAuthorization).where(OAuthAuthorization.code_hash == token_hash(code))
        if lock:
            query = query.with_for_update()
        return (await db.execute(query)).scalar_one_or_none()

    async def _live_token(
        self, db: AsyncSession, token: str, kind: str, *, lock: bool = False
    ) -> TokenRow | None:
        query = select(TokenRow).where(
            TokenRow.token_hash == token_hash(token),
            TokenRow.kind == kind,
            TokenRow.revoked_at.is_(None),
            TokenRow.expires_at > utcnow(),
        )
        if lock:
            query = query.with_for_update()
        row = (await db.execute(query)).scalar_one_or_none()
        if row is None or await live_member(db, row.organization_id, row.membership_id) is None:
            return None
        return row

    async def _revoke_for_authorization(self, db: AsyncSession, authorization_id: UUID) -> None:
        grants = set(
            (
                await db.execute(
                    select(TokenRow.grant_id).where(
                        TokenRow.authorization_id == authorization_id,
                        TokenRow.revoked_at.is_(None),
                    )
                )
            ).scalars()
        )
        for grant_id in grants:
            await revoke_grant(db, grant_id, "code_replayed")

    async def _issue(
        self,
        db: AsyncSession,
        *,
        grant_id: UUID,
        granted_at: datetime,
        authorization: OAuthAuthorization,
        client: OAuthClientInformationFull,
        granted: list[str],
    ) -> OAuthToken:
        """An access and a refresh token under one grant."""
        settings = self.ctx.settings
        organization_id, membership_id, user_id = _bound(authorization)
        now = utcnow()
        access, refresh = new_token(), new_token()
        lifetimes = {
            "access": timedelta(minutes=settings.oauth_access_token_minutes),
            "refresh": timedelta(days=settings.oauth_refresh_token_days),
        }
        for kind, token in (("access", access), ("refresh", refresh)):
            db.add(
                TokenRow(
                    organization_id=organization_id,
                    membership_id=membership_id,
                    user_id=user_id,
                    grant_id=grant_id,
                    authorization_id=authorization.id,
                    kind=kind,
                    token_hash=token_hash(token),
                    client_id=client.client_id,
                    client_name=client.client_name,
                    scopes=granted,
                    resource=authorization.resource,
                    granted_at=granted_at.astimezone(UTC),
                    expires_at=now + lifetimes[kind],
                )
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",  # noqa: S106 (a token type, not a password)
            expires_in=settings.oauth_access_token_minutes * 60,
            scope=" ".join(granted),
            refresh_token=refresh,
        )
