"""
The first organization of an instance: its storage, its Valkey, its first admin and its first
S3 key (DATABASE_SCHEMA.md, "Multi-tenancy", self-hosted). The key is probed before it is
stored, and stored `active`. A self-hosted instance refuses a second organization.
"""

from dataclasses import dataclass
from uuid import UUID, uuid7

from sqlalchemy import func, select

from floppy_server.config import MailBackend
from floppy_server.context import Context, utcnow
from floppy_server.infra.juicefs import S3Credentials
from floppy_server.keys.service import new_key_row
from floppy_server.models import Membership, Organization, OrganizationStatus, User


class BootstrapError(Exception):
    """The bootstrap cannot proceed; the message says why."""


@dataclass(frozen=True)
class BootstrapInput:
    """Everything the first organization needs."""

    organization_name: str
    slug: str
    admin_email: str
    s3_endpoint: str
    s3_region: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_key: str
    valkey_url: str
    valkey_admin_url: str | None = None
    valkey_databases: int = 16

    def __repr__(self) -> str:
        return f"BootstrapInput(organization={self.slug!r}, admin={self.admin_email!r})"


async def organization_count(ctx: Context) -> int:
    """How many live organizations the instance has."""
    async with ctx.sessions() as db:
        return int(
            await db.scalar(select(func.count()).where(Organization.deleted_at.is_(None))) or 0
        )


async def bootstrap(ctx: Context, data: BootstrapInput) -> UUID:
    """Creates the organization, its status row, the admin and the active key; returns its id."""
    if ctx.settings.single_organization and await organization_count(ctx) > 0:
        raise BootstrapError(
            "this instance already has its organization (FLOPPY_SINGLE_ORGANIZATION)"
        )
    if "@" in data.valkey_url or (data.valkey_admin_url and "@" in data.valkey_admin_url):
        raise BootstrapError(
            "Valkey URLs carry no credentials: set FLOPPY_VALKEY_ADMIN_PASSWORD instead"
        )
    email = data.admin_email.strip().lower()
    if "@" not in email[1:]:
        raise BootstrapError("the admin's email address is not valid")
    credentials = S3Credentials(
        access_key_id=data.s3_access_key_id.strip(), secret_key=data.s3_secret_key.strip()
    )
    result = await ctx.prober.probe(
        endpoint=data.s3_endpoint,
        region=data.s3_region,
        bucket=data.s3_bucket,
        key=credentials,
        label="bootstrap",
    )
    if not result.ok:
        raise BootstrapError(
            f"the test write with the S3 key failed: {result.code} on {result.request}"
        )
    now = utcnow()
    async with ctx.sessions() as db, db.begin():
        org = Organization(
            id=uuid7(),
            name=data.organization_name.strip(),
            slug=data.slug.strip(),
            s3_endpoint=data.s3_endpoint.strip(),
            s3_region=data.s3_region.strip(),
            s3_bucket=data.s3_bucket.strip(),
            valkey_url=data.valkey_url.strip(),
            valkey_admin_url=(data.valkey_admin_url or "").strip() or None,
            valkey_databases=data.valkey_databases,
        )
        db.add(org)
        await db.flush()
        db.add(
            OrganizationStatus(
                organization_id=org.id,
                storage_probed_at=now,
                mail_state="ok"
                if ctx.settings.mail_backend is MailBackend.CONSOLE or ctx.settings.smtp_host
                else "unconfigured",
            )
        )
        user = (
            await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=email)
            db.add(user)
            await db.flush()
        # The bootstrap admin is active from the start: there is nobody to invite them.
        db.add(
            Membership(
                organization_id=org.id,
                user_id=user.id,
                role="admin",
                status="active",
                activated_at=now,
            )
        )
        key = new_key_row(ctx, org.id, credentials.access_key_id, credentials.secret_key, None)
        key.state = "active"
        key.activated_at = now
        db.add(key)
        return org.id
