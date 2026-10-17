"""
The first organization of an instance: its storage, its Valkey, its first admin and its first
S3 key (DATABASE_SCHEMA.md, "Multi-tenancy", self-hosted). The key is probed before it is
stored, and stored `active`. A self-hosted instance refuses a second organization.
"""

import json
from dataclasses import dataclass
from uuid import UUID, uuid7

from sqlalchemy import func, select

from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.config import MailBackend
from floppy_server.context import Context, utcnow
from floppy_server.infra.juicefs import S3Credentials
from floppy_server.infra.providers import ProviderError
from floppy_server.keys.service import new_key_row
from floppy_server.models import Membership, Organization, OrganizationStatus, User


class BootstrapError(Exception):
    """The bootstrap cannot proceed; the message says why."""


@dataclass(frozen=True)
class BootstrapInput:
    """
    Everything the first organization needs. A manual organization brings its key (the team
    key, or the control plane's own with person keys); a managed one gets its bucket and key
    from the adapter, with the admin credentials given here or the instance's.
    """

    organization_name: str
    slug: str
    admin_email: str
    s3_endpoint: str
    s3_region: str
    s3_bucket: str
    valkey_url: str
    s3_access_key_id: str = ""
    s3_secret_key: str = ""
    valkey_admin_url: str | None = None
    valkey_databases: int = 16
    mail_language: str = "it"
    storage_mode: str = "manual"
    storage_provider: str | None = None
    storage_key_scope: str = "team"
    storage_admin_credentials: dict[str, str] | None = None

    def __repr__(self) -> str:
        return f"BootstrapInput(organization={self.slug!r}, admin={self.admin_email!r})"


async def organization_count(ctx: Context) -> int:
    """How many live organizations the instance has."""
    async with ctx.sessions() as db:
        return int(
            await db.scalar(select(func.count()).where(Organization.deleted_at.is_(None))) or 0
        )


def _check(ctx: Context, data: BootstrapInput) -> None:
    if "@" in data.valkey_url or (data.valkey_admin_url and "@" in data.valkey_admin_url):
        raise BootstrapError(
            "Valkey URLs carry no credentials: set FLOPPY_VALKEY_ADMIN_PASSWORD instead"
        )
    if "@" not in data.admin_email.strip()[1:]:
        raise BootstrapError("the admin's email address is not valid")
    if data.mail_language not in ("it", "en"):
        raise BootstrapError("the mail language is it or en")
    if data.storage_mode == "manual":
        if data.storage_key_scope not in ("team", "person"):
            raise BootstrapError("a manual organization has a team key or person keys")
        if data.storage_provider or data.storage_admin_credentials:
            raise BootstrapError("a manual organization has no provider adapter")
        if not data.s3_access_key_id.strip() or not data.s3_secret_key.strip():
            raise BootstrapError("a manual organization needs its S3 key")
    elif data.storage_mode == "managed":
        if data.storage_provider != "garage":
            raise BootstrapError("the managed adapters available are: garage")
        if data.storage_key_scope not in ("team", "person", "device"):
            raise BootstrapError("the key scope is team, person or device")
        if data.s3_access_key_id or data.s3_secret_key:
            raise BootstrapError("a managed organization gets its key from the provider")
    else:
        raise BootstrapError("the storage mode is manual or managed")


async def bootstrap(ctx: Context, data: BootstrapInput) -> UUID:
    """
    Creates the organization, its status row, the admin and the key the control plane uses
    (team or its own), and logs `organization_created`; returns the organization's id.
    """
    if ctx.settings.single_organization and await organization_count(ctx) > 0:
        raise BootstrapError(
            "this instance already has its organization (FLOPPY_SINGLE_ORGANIZATION)"
        )
    _check(ctx, data)
    email = data.admin_email.strip().lower()
    now = utcnow()
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
        mail_language=data.mail_language,
        storage_mode=data.storage_mode,
        storage_provider=data.storage_provider,
        storage_key_scope=data.storage_key_scope,
    )
    if data.storage_admin_credentials:
        org.storage_admin_credentials_enc = ctx.box.seal(
            json.dumps(data.storage_admin_credentials),
            table="organizations",
            column="storage_admin_credentials_enc",
            row_id=org.id,
        )
    provider_ref: str | None = None
    if data.storage_mode == "managed":
        try:
            adapter = ctx.adapter_for(org)
            await adapter.ensure_bucket(org.s3_bucket)
            created = await adapter.create_key(org.s3_bucket, f"floppy-{org.slug}-control-plane")
        except ProviderError as error:
            raise BootstrapError(f"the storage provider refused: {error}") from error
        credentials = S3Credentials(created.access_key_id, created.secret_key)
        provider_ref = created.provider_ref
    else:
        credentials = S3Credentials(data.s3_access_key_id.strip(), data.s3_secret_key.strip())
    result = await ctx.prober.probe(
        endpoint=org.s3_endpoint,
        region=org.s3_region,
        bucket=org.s3_bucket,
        key=credentials,
        label="bootstrap",
    )
    if not result.ok:
        raise BootstrapError(
            f"the test write with the S3 key failed: {result.code} on {result.request}"
        )
    async with ctx.sessions() as db, db.begin():
        db.add(org)
        await db.flush()
        db.add(
            OrganizationStatus(
                organization_id=org.id,
                storage_probed_at=now,
                bucket_versioning=result.versioning,
                bucket_old_versions_days=result.old_versions_days,
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
        key.holder = "team" if org.storage_key_scope == "team" else "control_plane"
        if provider_ref is not None:
            key.origin, key.provider_ref = "provider", provider_ref
        key.state = "active"
        key.activated_at = now
        db.add(key)
        record(
            db,
            org.id,
            "organization_created",
            AuditActor.operator("floppy-server bootstrap"),
            Target("organization", org.id, org.name),
            {"storageMode": org.storage_mode, "provider": org.storage_provider or ""},
        )
        return org.id
