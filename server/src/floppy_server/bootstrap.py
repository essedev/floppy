"""
The first organization of an instance: its storage mode, the default endpoint of its disks,
its first admin, and in managed mode the control plane's own key from the adapter
(DATABASE_SCHEMA.md, "Multi-tenancy", self-hosted). There is no bucket or Valkey instance to
give here: each disk gets its own when it is created. A self-hosted instance refuses a second
organization.
"""

import json
from dataclasses import dataclass
from uuid import UUID, uuid7

from sqlalchemy import func, select

from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.config import MailBackend
from floppy_server.context import Context, utcnow
from floppy_server.infra.providers import ProviderError
from floppy_server.keys.service import new_key_row
from floppy_server.models import Membership, Organization, OrganizationStatus, User


class BootstrapError(Exception):
    """The bootstrap cannot proceed; the message says why."""


@dataclass(frozen=True)
class BootstrapInput:
    """
    Everything the first organization needs. A managed one gets the control plane's key from
    the adapter, with the admin credentials given here or the instance's; a manual one brings
    a bucket and a key with each disk.
    """

    organization_name: str
    slug: str
    admin_email: str
    s3_endpoint: str
    s3_region: str
    mail_language: str = "it"
    storage_mode: str = "manual"
    storage_provider: str | None = None
    storage_admin_credentials: dict[str, str] | None = None

    def __repr__(self) -> str:
        return f"BootstrapInput(organization={self.slug!r}, admin={self.admin_email!r})"


async def organization_count(ctx: Context) -> int:
    """How many live organizations the instance has."""
    async with ctx.sessions() as db:
        return int(
            await db.scalar(select(func.count()).where(Organization.deleted_at.is_(None))) or 0
        )


def _check(data: BootstrapInput) -> None:
    if "@" not in data.admin_email.strip()[1:]:
        raise BootstrapError("the admin's email address is not valid")
    if data.mail_language not in ("it", "en"):
        raise BootstrapError("the mail language is it or en")
    if not data.s3_endpoint.strip():
        raise BootstrapError("the default S3 endpoint is missing")
    if data.storage_mode == "manual":
        if data.storage_provider or data.storage_admin_credentials:
            raise BootstrapError("a manual organization has no provider adapter")
    elif data.storage_mode == "managed":
        if data.storage_provider != "garage":
            raise BootstrapError("the managed adapters available are: garage")
    else:
        raise BootstrapError("the storage mode is manual or managed")


async def bootstrap(ctx: Context, data: BootstrapInput) -> UUID:
    """
    Creates the organization, its status row, the admin and, managed, the control plane's
    key, and logs `organization_created`; returns the organization's id.
    """
    if ctx.settings.single_organization and await organization_count(ctx) > 0:
        raise BootstrapError(
            "this instance already has its organization (FLOPPY_SINGLE_ORGANIZATION)"
        )
    _check(data)
    email = data.admin_email.strip().lower()
    now = utcnow()
    org = Organization(
        id=uuid7(),
        name=data.organization_name.strip(),
        slug=data.slug.strip(),
        s3_endpoint=data.s3_endpoint.strip(),
        s3_region=data.s3_region.strip(),
        mail_language=data.mail_language,
        storage_mode=data.storage_mode,
        storage_provider=data.storage_provider,
    )
    if data.storage_admin_credentials:
        org.storage_admin_credentials_enc = ctx.box.seal(
            json.dumps(data.storage_admin_credentials),
            table="organizations",
            column="storage_admin_credentials_enc",
            row_id=org.id,
        )
    created = None
    if data.storage_mode == "managed":
        try:
            created = await ctx.adapter_for(org).create_key(f"floppy-{org.slug}-control-plane")
        except ProviderError as error:
            raise BootstrapError(f"the storage provider refused: {error}") from error
    async with ctx.sessions() as db, db.begin():
        db.add(org)
        await db.flush()
        db.add(
            OrganizationStatus(
                organization_id=org.id,
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
        if created is not None:
            key = new_key_row(
                ctx,
                org.id,
                created.access_key_id,
                created.secret_key,
                holder="control_plane",
                user_id=None,
            )
            key.provider_ref = created.provider_ref
            key.state, key.activated_at = "active", now
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
