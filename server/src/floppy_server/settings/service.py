"""
The organization's settings: its name, its SMTP server and its storage, with a mail test and
a storage probe.

The SMTP password is write only: only whether one is set comes back. An empty `host` means
the organization uses the instance's SMTP server (the environment's). Storage is frozen once
a live disk exists: a volume's bucket and endpoint are in its format and do not follow the
settings (DATABASE_SCHEMA.md, decision 9).
"""

from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from floppy_server.api import Actor, CamelModel
from floppy_server.audit.service import AuditActor, Target, record
from floppy_server.auth.service import mail_failed, mark_mail_state
from floppy_server.context import Context, utcnow
from floppy_server.errors import ApiError, invalid_state, not_found
from floppy_server.infra.mail import MailError
from floppy_server.keys.service import active_key
from floppy_server.mailtext import mail_test
from floppy_server.models import Organization, OrganizationStatus, Volume

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Optional = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class MailSettings(CamelModel):
    """The organization's SMTP server; an empty host means the instance's."""

    host: str
    port: int
    username: str
    sender: str
    password_set: bool


class StorageSettings(CamelModel):
    """The organization's bucket."""

    endpoint: str
    region: str
    bucket: str


class SettingsOut(CamelModel):
    """The settings page."""

    organization_name: str
    mail: MailSettings
    storage: StorageSettings
    storage_locked: bool


class MailSettingsIn(CamelModel):
    """An SMTP server (an empty host returns to the instance's); an empty password keeps the old."""

    host: Optional
    port: int = Field(ge=0, le=65535)
    username: Optional = ""
    sender: Optional = ""
    password: Annotated[str, StringConstraints(max_length=1024)] | None = None


class StorageIn(CamelModel):
    """A bucket to use or to probe."""

    endpoint: Text
    region: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    bucket: Text


class UpdateSettingsRequest(CamelModel):
    """Omitted fields stay as they are."""

    organization_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    mail: MailSettingsIn | None = None
    storage: StorageIn | None = None


def storage_in_use(disks: int) -> ApiError:
    """409: storage cannot change while a live disk exists."""
    return ApiError(
        409, "storage_in_use", "Storage cannot change while disks exist", {"disks": disks}
    )


async def _live_disks(db: AsyncSession, organization_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).where(
                Volume.organization_id == organization_id, Volume.deleted_at.is_(None)
            )
        )
        or 0
    )


async def get_settings(ctx: Context, organization_id: UUID) -> SettingsOut:
    """The API shape of an organization's settings."""
    async with ctx.sessions() as db:
        org = await db.get(Organization, organization_id)
        if org is None:
            raise not_found()
        disks = await _live_disks(db, organization_id)
    return SettingsOut(
        organization_name=org.name,
        mail=MailSettings(
            host=org.smtp_host or "",
            port=org.smtp_port or 0,
            username=org.smtp_username or "",
            sender=org.smtp_sender or "",
            password_set=org.smtp_password_enc is not None,
        ),
        storage=StorageSettings(
            endpoint=org.s3_endpoint, region=org.s3_region, bucket=org.s3_bucket
        ),
        storage_locked=disks > 0,
    )


def _apply_mail(ctx: Context, org: Organization, mail: MailSettingsIn) -> list[str]:
    """Writes the SMTP columns; returns the names of what changed, never values of secrets."""
    if not mail.host:
        if org.smtp_host is None:
            return []
        org.smtp_host = org.smtp_port = org.smtp_username = org.smtp_sender = None
        org.smtp_password_enc = None
        return ["mail"]
    if not 1 <= mail.port <= 65535 or not mail.sender:
        raise ApiError(
            422, "validation", "A mail server needs a port and a sender", {"field": "mail"}
        )
    changed = [
        f"mail.{name}"
        for name, old, new in (
            ("host", org.smtp_host, mail.host),
            ("port", org.smtp_port, mail.port),
            ("username", org.smtp_username, mail.username or None),
            ("sender", org.smtp_sender, mail.sender),
        )
        if old != new
    ]
    org.smtp_host, org.smtp_port = mail.host, mail.port
    org.smtp_username, org.smtp_sender = mail.username or None, mail.sender
    if mail.password:
        org.smtp_password_enc = ctx.box.seal(
            mail.password, table="organizations", column="smtp_password_enc", row_id=org.id
        )
        changed.append("mail.password")
    return changed


async def update_settings(ctx: Context, actor: Actor, body: UpdateSettingsRequest) -> SettingsOut:
    """Applies the changes and logs the names of the fields that changed."""
    fields: list[str] = []
    async with ctx.sessions() as db, db.begin():
        org = (
            await db.execute(
                select(Organization)
                .where(Organization.id == actor.organization_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if org is None:
            raise not_found()
        if body.organization_name is not None and body.organization_name != org.name:
            org.name = body.organization_name
            fields.append("organizationName")
        if body.mail is not None:
            fields.extend(_apply_mail(ctx, org, body.mail))
        if body.storage is not None:
            new = (body.storage.endpoint, body.storage.region, body.storage.bucket)
            if new != (org.s3_endpoint, org.s3_region, org.s3_bucket):
                disks = await _live_disks(db, org.id)
                if disks:
                    raise storage_in_use(disks)
                org.s3_endpoint, org.s3_region, org.s3_bucket = new
                fields.append("storage")
        if fields:
            record(
                db,
                org.id,
                "settings_updated",
                AuditActor.person(actor.user_id, actor.label),
                Target("organization", org.id, org.name),
                {"fields": fields},
            )
            try:
                await db.flush()
            except IntegrityError as error:
                raise ApiError(
                    409, "validation", "Another organization uses this bucket", {"field": "storage"}
                ) from error
    return await get_settings(ctx, actor.organization_id)


async def test_mail(ctx: Context, actor: Actor) -> str:
    """Sends a test mail to the admin; returns the address."""
    async with ctx.sessions() as db:
        org = await db.get(Organization, actor.organization_id)
    if org is None:
        raise not_found()
    try:
        await ctx.mailer.send(mail_test(to=actor.email, organization=org.name), ctx.smtp_for(org))
    except MailError as error:
        await mark_mail_state(ctx, org.id, ok=False)
        raise mail_failed(error) from error
    async with ctx.sessions() as db, db.begin():
        await db.execute(
            update(OrganizationStatus)
            .where(OrganizationStatus.organization_id == org.id)
            .values(mail_state="ok", mail_sent_at=utcnow())
        )
    return actor.email


async def probe_storage(ctx: Context, actor: Actor, body: StorageIn) -> None:
    """A test write with the organization's key on a bucket; raises `probe_failed`."""
    async with ctx.sessions() as db:
        key = await active_key(db, actor.organization_id)
    if key is None:
        raise invalid_state("The organization has no active S3 key")
    result = await ctx.prober.probe(
        endpoint=body.endpoint,
        region=body.region,
        bucket=body.bucket,
        key=ctx.credentials(key),
        label="settings",
    )
    async with ctx.sessions() as db, db.begin():
        await db.execute(
            update(OrganizationStatus)
            .where(OrganizationStatus.organization_id == actor.organization_id)
            .values(storage_probed_at=utcnow(), storage_state="ok" if result.ok else "failing")
        )
    if not result.ok:
        raise ApiError(
            422,
            "probe_failed",
            "The test write failed",
            {"code": result.code or "", "request": result.request or ""},
        )
