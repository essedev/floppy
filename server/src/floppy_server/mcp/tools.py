"""
The MCP surface's tools: the admin web's operations, each calling the same router function
the REST API calls, so a tool and its endpoint cannot drift apart.

Each tool declares its arguments (a pydantic model, snake_case), its hints (the scope it
needs, oauth/scopes.py) and whether it is for admins. Tools that would carry an S3 key or a
password (a manual disk's bucket key, a pasted rotation key, the SMTP password, the storage
probe) are left out on purpose: a secret typed into a chat ends up in the model's context
and in the client's transcript. Those stay in the admin web.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from floppy_server.access import router as access_router
from floppy_server.access.service import AccessEntry, DiskEntry, SetAccessRequest
from floppy_server.activity.service import person_history
from floppy_server.api import Actor, Email
from floppy_server.audit import router as audit_router
from floppy_server.context import Context
from floppy_server.devices import router as devices_router
from floppy_server.disks import router as disks_router
from floppy_server.disks.service import (
    DISK_NAME_PATTERN,
    CachePolicy,
    CreateDiskRequest,
    UpdateDiskRequest,
)
from floppy_server.errors import ApiError
from floppy_server.health import router as health_router
from floppy_server.keys import router as keys_router
from floppy_server.keys.schemas import CloseRotationRequest
from floppy_server.mcp.hints import CREATES, DESTROYS, PROBES, READS, UPDATES, ToolHints
from floppy_server.models import Organization
from floppy_server.people import router as people_router
from floppy_server.people.service import get_person
from floppy_server.settings import router as settings_router
from floppy_server.settings.service import MailSettingsIn, StorageIn, UpdateSettingsRequest


class Args(BaseModel):
    """A tool's arguments: unknown keys are refused, not ignored."""

    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    """No arguments."""


class DiskId(Args):
    disk_id: UUID = Field(description="The disk's id, from list_disks or list_my_disks.")


class PersonId(Args):
    person_id: UUID = Field(description="The person's id, from list_people.")


class DeviceId(Args):
    device_id: UUID = Field(description="The device's id, from list_devices.")


class RotationId(Args):
    rotation_id: UUID = Field(description="The rotation's id, from get_key_status.")


class DiskActivityArgs(Args):
    disk_id: UUID = Field(description="The disk's id, from list_my_disks.")
    path: str | None = Field(
        default=None,
        max_length=1024,
        description="Keep events whose path contains this text, case-insensitive.",
    )
    before: str | None = Field(
        default=None, max_length=200, description="The `before` of the previous page."
    )
    limit: int = Field(default=50, ge=1, le=200)


class AccessIn(Args):
    person_id: UUID
    access: Literal["none", "read", "write"]


class CreateDiskArgs(Args):
    name: str = Field(
        pattern=DISK_NAME_PATTERN,
        description="The disk's folder name in /Volumes/Floppy on every Mac: letters, digits, spaces, dots, dashes.",
    )
    trash_days: int = Field(
        default=7, ge=0, le=365, description="Days deleted files stay in the trash."
    )
    access: list[AccessIn] = Field(
        default_factory=list,
        max_length=500,
        description="Who sees the disk; you get write unless listed.",
    )


class UpdateDiskArgs(Args):
    disk_id: UUID
    trash_days: int | None = Field(default=None, ge=0, le=365)
    cache_expire_days: int | None = Field(
        default=None, ge=1, le=365, description="Days a Mac keeps an unread copy of a file."
    )


class DiskAccessIn(Args):
    disk_id: UUID
    access: Literal["none", "read", "write"]


class InviteArgs(Args):
    email: Email
    role: Literal["admin", "member"]
    disks: list[DiskAccessIn] = Field(
        default_factory=list,
        max_length=500,
        description="The disks they will see; none unless listed.",
    )


class RoleArgs(Args):
    person_id: UUID
    role: Literal["admin", "member"]


class SetAccessArgs(Args):
    disk_id: UUID
    person_id: UUID
    access: Literal["none", "read", "write"] = Field(
        description="`none` takes the disk away from their Macs at once; `read` mounts it read-only."
    )


class AuditArgs(Args):
    since_days: Literal[7, 30, 90] = 30
    category: Literal["access", "people", "devices", "key", "disks", "settings"] | None = None
    query: str | None = Field(default=None, max_length=100, description="Text in who or what.")
    cursor: str | None = Field(
        default=None, max_length=200, description="The previous page's `nextCursor`."
    )
    limit: int = Field(default=20, ge=1, le=100)


class SettingsArgs(Args):
    organization_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    mail_language: Literal["it", "en"] | None = Field(
        default=None, description="The language of invitations and sign-in mail."
    )
    smtp_host: str | None = Field(
        default=None, max_length=255, description="Empty returns to the instance's server."
    )
    smtp_port: int | None = Field(default=None, ge=0, le=65535)
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_sender: str | None = Field(default=None, max_length=255)
    storage_endpoint: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="The S3 endpoint of new disks; only while no disk exists.",
    )
    storage_region: str | None = Field(default=None, max_length=100)


Handler = Callable[[Context, Actor, Any], Awaitable[object]]


@dataclass(frozen=True)
class Tool:
    """One tool of the surface."""

    name: str
    title: str
    description: str
    args: type[Args]
    hints: ToolHints
    admin: bool
    run: Handler


async def _whoami(ctx: Context, actor: Actor, _: NoArgs) -> object:
    async with ctx.sessions() as db:
        org = await db.scalar(select(Organization).where(Organization.id == actor.organization_id))
    return {
        "person": {"id": str(actor.user_id), "email": actor.email, "name": actor.name},
        "role": actor.role,
        "organization": {"name": org.name if org else None, "slug": org.slug if org else None},
    }


async def _create_disk(ctx: Context, actor: Actor, args: CreateDiskArgs) -> object:
    async with ctx.sessions() as db:
        mode = await db.scalar(
            select(Organization.storage_mode).where(Organization.id == actor.organization_id)
        )
    if mode != "managed":
        # Manual mode needs the disk's own bucket key: a secret that does not belong in a chat.
        raise ApiError(
            409,
            "manual_storage",
            "This organization uses manual storage: a disk needs its bucket's key, which is "
            "pasted in the admin web (Dischi > Nuovo disco), not here",
        )
    body = CreateDiskRequest(
        name=args.name,
        trash_days=args.trash_days,
        access=[AccessEntry(person_id=a.person_id, access=a.access) for a in args.access],
    )
    return await disks_router.create_disk(body, actor, ctx)


async def _update_disk(ctx: Context, actor: Actor, args: UpdateDiskArgs) -> object:
    cache = (
        None if args.cache_expire_days is None else CachePolicy(expire_days=args.cache_expire_days)
    )
    body = UpdateDiskRequest(trash_days=args.trash_days, cache=cache)
    return await disks_router.update_disk(args.disk_id, body, actor, ctx)


async def _delete_disk(ctx: Context, actor: Actor, args: DiskId) -> object:
    await disks_router.delete_disk(args.disk_id, actor, ctx)
    return {"deleted": str(args.disk_id)}


async def _probe_disk(ctx: Context, actor: Actor, args: DiskId) -> object:
    await disks_router.probe(args.disk_id, actor, ctx)
    return await disks_router.get_disk(args.disk_id, actor, ctx)


async def _invite(ctx: Context, actor: Actor, args: InviteArgs) -> object:
    body = people_router.InviteRequest(
        email=args.email,
        role=args.role,
        disks=[DiskEntry(disk_id=d.disk_id, access=d.access) for d in args.disks],
    )
    return await people_router.invite(body, actor, ctx)


async def _change_role(ctx: Context, actor: Actor, args: RoleArgs) -> object:
    body = people_router.UpdatePersonRequest(role=args.role)
    return await people_router.update_person(args.person_id, body, actor, ctx)


async def _set_access(ctx: Context, actor: Actor, args: SetAccessArgs) -> object:
    body = SetAccessRequest(access=args.access)
    return await access_router.set_access(args.disk_id, args.person_id, body, actor, ctx)


async def _audit(ctx: Context, actor: Actor, args: AuditArgs) -> object:
    return await audit_router.get_audit(
        actor,
        ctx,
        since_days=args.since_days,
        category=args.category,
        q=args.query,
        cursor=args.cursor,
        limit=args.limit,
    )


async def _close_rotation(ctx: Context, actor: Actor, args: RotationId) -> object:
    body = CloseRotationRequest(old_key_deleted=True)
    return await keys_router.close_rotation(args.rotation_id, body, actor, ctx)


async def _update_settings(ctx: Context, actor: Actor, args: SettingsArgs) -> object:
    mail = None
    smtp = (args.smtp_host, args.smtp_port, args.smtp_username, args.smtp_sender)
    if any(value is not None for value in smtp):
        current = (await settings_router.get_settings(actor, ctx)).mail
        mail = MailSettingsIn(
            host=current.host if args.smtp_host is None else args.smtp_host,
            port=current.port if args.smtp_port is None else args.smtp_port,
            username=current.username if args.smtp_username is None else args.smtp_username,
            sender=current.sender if args.smtp_sender is None else args.smtp_sender,
            password=None,
        )
    storage = None
    if args.storage_endpoint is not None or args.storage_region is not None:
        current_storage = (await settings_router.get_settings(actor, ctx)).storage
        storage = StorageIn(
            endpoint=args.storage_endpoint or current_storage.endpoint,
            region=current_storage.region if args.storage_region is None else args.storage_region,
        )
    body = UpdateSettingsRequest(
        organization_name=args.organization_name,
        mail_language=args.mail_language,
        mail=mail,
        storage=storage,
    )
    return await settings_router.update_settings(body, actor, ctx)


async def _test_mail(ctx: Context, actor: Actor, _: NoArgs) -> object:
    return await settings_router.test_mail(actor, ctx)


TOOLS: tuple[Tool, ...] = (
    # Everyone
    Tool(
        "whoami",
        "Who am I",
        "The signed-in person, their role (admin or member) and their organization. Call it "
        "first: members see only their own disks and devices.",
        NoArgs,
        READS,
        False,
        _whoami,
    ),
    Tool(
        "list_my_disks",
        "My disks",
        "The disks you may read or write, with your access. Each is a folder in "
        "/Volumes/Floppy on your Mac.",
        NoArgs,
        READS,
        False,
        lambda ctx, actor, _: access_router.my_disks(actor, ctx),
    ),
    Tool(
        "disk_activity",
        "Disk activity",
        "Who created, changed, renamed or deleted what on one of your disks, newest first, "
        "for the last 30 days. Paths are inside the disk, as the Mac's VM names them: a "
        "character the Finder forbids (like `/` in a name) shows as one in U+F001-F027.",
        DiskActivityArgs,
        READS,
        False,
        lambda ctx, actor, a: person_history(ctx, actor, a.disk_id, a.before, a.limit, a.path),
    ),
    Tool(
        "list_devices",
        "Devices",
        "The Macs signed in to Floppy: all of them for an admin, your own for a member, with "
        "their state, app version and last contact.",
        NoArgs,
        READS,
        False,
        lambda ctx, actor, _: devices_router.list_devices(actor, ctx),
    ),
    Tool(
        "revoke_device",
        "Revoke a device",
        "Disconnects a Mac from every disk at once (a lost or sold Mac). A member may revoke "
        "only their own. The Mac must sign in again to come back.",
        DeviceId,
        DESTROYS,
        False,
        lambda ctx, actor, a: devices_router.revoke_device(a.device_id, actor, ctx),
    ),
    # Admins: disks
    Tool(
        "list_disks",
        "Disks",
        "The organization's disks with their state, bucket, size, trash and how many Macs mount them.",
        NoArgs,
        READS,
        True,
        lambda ctx, actor, _: disks_router.list_disks(actor, ctx),
    ),
    Tool(
        "get_disk",
        "Disk",
        "One disk, also while it is being created or after its creation failed.",
        DiskId,
        READS,
        True,
        lambda ctx, actor, a: disks_router.get_disk(a.disk_id, actor, ctx),
    ),
    Tool(
        "create_disk",
        "Create a disk",
        "Creates a disk in managed storage (the bucket is made for it). It starts as "
        "`creating`: call get_disk until `ready` or `failed`. Manual storage needs a bucket "
        "key, pasted in the admin web.",
        CreateDiskArgs,
        CREATES,
        True,
        _create_disk,
    ),
    Tool(
        "update_disk",
        "Change a disk",
        "Changes a disk's trash days or how long Macs keep unread copies; omitted fields stay.",
        UpdateDiskArgs,
        UPDATES,
        True,
        _update_disk,
    ),
    Tool(
        "delete_disk",
        "Delete a disk",
        "Removes a disk from every Mac. Its bucket and objects stay at the provider; nothing "
        "is erased. Ask the user to confirm the disk's name first.",
        DiskId,
        DESTROYS,
        True,
        _delete_disk,
    ),
    Tool(
        "probe_disk",
        "Test a disk's storage",
        "A test write on the disk's bucket; returns the disk with the result in its storage status.",
        DiskId,
        PROBES,
        True,
        _probe_disk,
    ),
    # Admins: people and access
    Tool(
        "list_people",
        "People",
        "Everyone in the organization with role and state (invited, active, removed).",
        NoArgs,
        READS,
        True,
        lambda ctx, actor, _: people_router.list_people(actor, ctx),
    ),
    Tool(
        "get_person",
        "Person",
        "One person with role, state and devices.",
        PersonId,
        READS,
        True,
        lambda ctx, actor, a: get_person(ctx, actor, a.person_id),
    ),
    Tool(
        "invite_person",
        "Invite a person",
        "Sends an invitation by mail with a role and the disks they will see.",
        InviteArgs,
        CREATES,
        True,
        _invite,
    ),
    Tool(
        "resend_invitation",
        "Resend an invitation",
        "Sends a new invitation link to someone who has not joined yet.",
        PersonId,
        CREATES,
        True,
        lambda ctx, actor, a: people_router.resend(a.person_id, actor, ctx),
    ),
    Tool(
        "change_role",
        "Change a role",
        "Makes a person admin or member; the last admin cannot step down.",
        RoleArgs,
        UPDATES,
        True,
        _change_role,
    ),
    Tool(
        "remove_person",
        "Remove a person",
        "Removes a person from the organization: their sessions end and their Macs are "
        "revoked at once. Ask the user to confirm the person first.",
        PersonId,
        DESTROYS,
        True,
        lambda ctx, actor, a: people_router.remove_person(a.person_id, actor, ctx),
    ),
    Tool(
        "list_access",
        "Access",
        "Every disk and person with the person's access: none, read or write.",
        NoArgs,
        READS,
        True,
        lambda ctx, actor, _: access_router.list_access(actor, ctx),
    ),
    Tool(
        "set_access",
        "Set access",
        "Sets one person's access to one disk, applied live to their Macs.",
        SetAccessArgs,
        UPDATES,
        True,
        _set_access,
    ),
    # Admins: key, log, settings, health
    Tool(
        "get_key_status",
        "Storage keys",
        "Each disk's S3 key, its open exposure window (a removed person or revoked Mac had "
        "it) and any rotation in progress.",
        NoArgs,
        READS,
        True,
        lambda ctx, actor, _: keys_router.get_key(actor, ctx),
    ),
    Tool(
        "get_key_rotation",
        "Key rotation",
        "A rotation's progress, step by step.",
        RotationId,
        READS,
        True,
        lambda ctx, actor, a: keys_router.get_rotation(a.rotation_id, actor, ctx),
    ),
    Tool(
        "close_key_rotation",
        "Close a key rotation",
        "Closes a rotation once the admin has deleted the old key at the provider. Only after "
        "the user says they deleted it: closing does not delete anything.",
        RotationId,
        UPDATES,
        True,
        _close_rotation,
    ),
    Tool(
        "get_audit_log",
        "Audit log",
        "What happened in the organization, newest first: sign-ins, people, devices, keys, "
        "disks and settings, with who did it.",
        AuditArgs,
        READS,
        True,
        _audit,
    ),
    Tool(
        "get_settings",
        "Settings",
        "The organization's name, mail language, SMTP server and default storage.",
        NoArgs,
        READS,
        True,
        lambda ctx, actor, _: settings_router.get_settings(actor, ctx),
    ),
    Tool(
        "update_settings",
        "Change settings",
        "Changes the organization's name, mail language, SMTP server (not its password: that "
        "is set in the admin web) or default storage; omitted fields stay.",
        SettingsArgs,
        UPDATES,
        True,
        _update_settings,
    ),
    Tool(
        "send_test_mail",
        "Send a test mail",
        "Sends a test mail to you through the organization's SMTP server.",
        NoArgs,
        PROBES,
        True,
        _test_mail,
    ),
    Tool(
        "get_health",
        "Health",
        "Each disk's metadata and bucket and the mail server as last checked, with a summary.",
        NoArgs,
        READS,
        True,
        lambda ctx, actor, _: health_router.get_health(actor, ctx),
    ),
)

BY_NAME = {tool.name: tool for tool in TOOLS}


def visible_to(actor: Actor) -> list[Tool]:
    """The tools a person's role may call, in the fixed order of `TOOLS`."""
    return [tool for tool in TOOLS if actor.is_admin or not tool.admin]
