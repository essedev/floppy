"""
The control plane's tables, as docs/DATABASE_SCHEMA.md defines them.

Tenant tables carry `organization_id` with a `UNIQUE (organization_id, id)`, and every
reference between them is a composite foreign key, so a row can never point at another
organization's row. There are no ORM relationships on purpose: nothing here can cascade
into a `DELETE`, which the application role is not granted anyway.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from floppy_server.db import Base

UUIDV7 = text("uuidv7()")
NOW = text("now()")


def _id() -> Mapped[UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, server_default=UUIDV7)


def _ts(*, nullable: bool = False, default: bool = False) -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=nullable, server_default=NOW if default else None
    )


def _in(column: str, *values: str) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _tenant_fk(columns: list[str], table: str, name: str | None = None) -> ForeignKeyConstraint:
    """A composite foreign key `(organization_id, x_id) -> table (organization_id, id)`."""
    return ForeignKeyConstraint(
        ["organization_id", *columns],
        [f"{table}.organization_id", f"{table}.id"],
        name=name,
    )


def _tenant_unique(table: str) -> UniqueConstraint:
    return UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id")


# Enumerations, as CHECK lists (text, not PostgreSQL ENUM types).
ROLES = ("admin", "member")
MEMBERSHIP_STATES = ("invited", "active", "removed")
INVITATION_STATES = ("sent", "accepted", "superseded", "revoked")
SIGN_IN_KINDS = ("web", "app")
SIGN_IN_STATES = ("pending", "completed", "locked", "superseded")
SESSION_REVOKE_REASONS = ("signed_out", "membership_removed")
DEVICE_STATES = ("pending", "active", "revoked")
DEVICE_REVOKE_REASONS = ("admin", "owner", "member_removed", "signed_out", "never_registered")
REPLICA_MODES = ("replica", "master", "offline")
VOLUME_STATES = ("creating", "ready", "failed")
KEY_STATES = ("pending", "active", "retiring", "retired", "rejected")
ROTATION_STATES = ("applying", "awaiting_old_key_deletion", "failed", "superseded", "closed")
STEP_STATES = ("pending", "running", "done", "failed")
EXPOSURE_KINDS = ("person_removed", "mac_revoked")
ACTOR_KINDS = ("person", "system", "operator")
AUDIT_CATEGORIES = ("access", "people", "macs", "key", "disks", "settings")
AUDIT_TARGETS = ("person", "mac", "disk", "key", "organization")
AUDIT_ACTIONS = (
    "signed_in",
    "sign_in_locked",
    "person_invited",
    "invite_resent",
    "invite_accepted",
    "role_changed",
    "person_removed",
    "mac_registered",
    "mac_revoked",
    "mac_signed_out",
    "mac_seen_outdated",
    "key_rotation_started",
    "key_rotated",
    "key_rotation_failed",
    "key_window_closed",
    "disk_created",
    "disk_updated",
    "settings_updated",
)


class Organization(Base):
    """A tenant: a team with its bucket and its Valkey instance."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'", name="slug_format"),
        CheckConstraint("position('@' in valkey_url) = 0", name="valkey_url_no_credentials"),
        CheckConstraint("valkey_databases BETWEEN 2 AND 1024", name="valkey_databases_range"),
        CheckConstraint("smtp_port BETWEEN 1 AND 65535", name="smtp_port_range"),
        CheckConstraint("(smtp_host IS NULL) = (smtp_port IS NULL)", name="smtp_host_port"),
        UniqueConstraint("slug", name="uq_organizations_slug"),
        Index(
            "uq_organizations_valkey_url",
            text("lower(valkey_url)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_organizations_s3_endpoint_s3_bucket",
            "s3_endpoint",
            "s3_bucket",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = _id()
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text)
    s3_endpoint: Mapped[str] = mapped_column(Text)
    s3_region: Mapped[str] = mapped_column(Text)
    s3_bucket: Mapped[str] = mapped_column(Text)
    valkey_url: Mapped[str] = mapped_column(Text)
    valkey_admin_url: Mapped[str | None] = mapped_column(Text)
    valkey_admin_username: Mapped[str | None] = mapped_column(Text)
    valkey_admin_password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    valkey_databases: Mapped[int] = mapped_column(Integer, server_default=text("16"))
    smtp_host: Mapped[str | None] = mapped_column(Text)
    smtp_port: Mapped[int | None] = mapped_column(Integer)
    smtp_username: Mapped[str | None] = mapped_column(Text)
    smtp_sender: Mapped[str | None] = mapped_column(Text)
    smtp_password_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = _ts(nullable=True)


class OrganizationStatus(Base):
    """What the control plane last saw of an organization's services, for `GET /health`."""

    __tablename__ = "organization_status"
    __table_args__ = (
        CheckConstraint(_in("metadata_state", "ok", "down"), name="metadata_state"),
        CheckConstraint(_in("storage_state", "ok", "failing"), name="storage_state"),
        CheckConstraint(_in("mail_state", "ok", "unconfigured", "failing"), name="mail_state"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    metadata_state: Mapped[str] = mapped_column(Text, server_default=text("'ok'"))
    metadata_latency_ms: Mapped[int | None] = mapped_column(Integer)
    metadata_checked_at: Mapped[datetime | None] = _ts(nullable=True)
    storage_state: Mapped[str] = mapped_column(Text, server_default=text("'ok'"))
    storage_probed_at: Mapped[datetime | None] = _ts(nullable=True)
    mail_state: Mapped[str] = mapped_column(Text, server_default=text("'unconfigured'"))
    mail_sent_at: Mapped[datetime | None] = _ts(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )


class User(Base):
    """A person, identified by an email address. Global, not per organization."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email = lower(btrim(email)) AND position('@' in email) > 1", name="email_normalized"
        ),
        CheckConstraint("length(name) <= 100", name="name_length"),
        Index("uq_users_email", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    id: Mapped[UUID] = _id()
    email: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    last_sign_in_at: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = _ts(nullable=True)


class Membership(Base):
    """A user in an organization, with a role. The API's `Person` is a user plus this row."""

    __tablename__ = "memberships"
    __table_args__ = (
        _tenant_unique("memberships"),
        CheckConstraint(_in("role", *ROLES), name="role"),
        CheckConstraint(_in("status", *MEMBERSHIP_STATES), name="status"),
        CheckConstraint("(status = 'removed') = (removed_at IS NOT NULL)", name="removed_at"),
        CheckConstraint("status <> 'active' OR activated_at IS NOT NULL", name="active_activated"),
        CheckConstraint(
            "status <> 'invited' OR activated_at IS NULL", name="invited_not_activated"
        ),
        Index(
            "uq_memberships_organization_id_user_id",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text("status <> 'removed' AND deleted_at IS NULL"),
        ),
        Index(
            "ix_memberships_user_id_live",
            "user_id",
            postgresql_where=text("status <> 'removed' AND deleted_at IS NULL"),
        ),
        Index("ix_memberships_organization_id_status", "organization_id", "status"),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'invited'"))
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    invited_at: Mapped[datetime | None] = _ts(nullable=True)
    activated_at: Mapped[datetime | None] = _ts(nullable=True)
    removed_at: Mapped[datetime | None] = _ts(nullable=True)
    removed_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = _ts(nullable=True)


class Invitation(Base):
    """One invitation mail: the first one and every resend."""

    __tablename__ = "invitations"
    __table_args__ = (
        _tenant_unique("invitations"),
        _tenant_fk(["membership_id"], "memberships"),
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint(_in("state", *INVITATION_STATES), name="state"),
        CheckConstraint("expires_at > sent_at", name="expires_after_sent"),
        CheckConstraint("(state = 'accepted') = (accepted_at IS NOT NULL)", name="accepted_at"),
        CheckConstraint(
            "(state = 'superseded') = (superseded_at IS NOT NULL)", name="superseded_at"
        ),
        CheckConstraint("(state = 'revoked') = (revoked_at IS NOT NULL)", name="revoked_at"),
        Index(
            "uq_invitations_membership_id_sent",
            "membership_id",
            unique=True,
            postgresql_where=text("state = 'sent'"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    membership_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    state: Mapped[str] = mapped_column(Text, server_default=text("'sent'"))
    sent_by_user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    sent_at: Mapped[datetime] = _ts(default=True)
    expires_at: Mapped[datetime] = _ts()
    accepted_at: Mapped[datetime | None] = _ts(nullable=True)
    superseded_at: Mapped[datetime | None] = _ts(nullable=True)
    revoked_at: Mapped[datetime | None] = _ts(nullable=True)


class SignInRequest(Base):
    """A pending sign-in: the magic link and the 6-digit code, for the web or the app."""

    __tablename__ = "sign_in_requests"
    __table_args__ = (
        CheckConstraint(_in("kind", *SIGN_IN_KINDS), name="kind"),
        CheckConstraint(_in("state", *SIGN_IN_STATES), name="state"),
        CheckConstraint("email = lower(btrim(email))", name="email_normalized"),
        CheckConstraint("code_attempts BETWEEN 0 AND 5", name="code_attempts_range"),
        CheckConstraint(_in("completed_via", "link", "code"), name="completed_via"),
        CheckConstraint(
            "redirect_uri ~ '^http://127\\.0\\.0\\.1:[0-9]{1,5}/'", name="redirect_uri_loopback"
        ),
        CheckConstraint(
            "(kind = 'app') = (code_challenge IS NOT NULL AND redirect_uri IS NOT NULL AND device_name IS NOT NULL)",
            name="app_fields",
        ),
        CheckConstraint("(state = 'completed') = (completed_at IS NOT NULL)", name="completed_at"),
        CheckConstraint("(state = 'locked') = (locked_at IS NOT NULL)", name="locked_at"),
        CheckConstraint(
            "(state = 'superseded') = (superseded_at IS NOT NULL)", name="superseded_at"
        ),
        CheckConstraint("octet_length(link_token_hash) = 32", name="link_token_hash_length"),
        UniqueConstraint("link_token_hash", name="uq_sign_in_requests_link_token_hash"),
        UniqueConstraint("code_challenge", name="uq_sign_in_requests_code_challenge"),
        UniqueConstraint("auth_code_hash", name="uq_sign_in_requests_auth_code_hash"),
        Index("ix_sign_in_requests_email_created_at", "email", text("created_at DESC")),
    )

    id: Mapped[UUID] = _id()
    kind: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    organization_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    link_token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    code_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    code_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    expires_at: Mapped[datetime] = _ts()
    resend_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = _ts(nullable=True)
    completed_via: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = _ts(nullable=True)
    superseded_at: Mapped[datetime | None] = _ts(nullable=True)
    device_name: Mapped[str | None] = mapped_column(Text)
    code_challenge: Mapped[str | None] = mapped_column(Text)
    redirect_uri: Mapped[str | None] = mapped_column(Text)
    auth_code_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    auth_code_expires_at: Mapped[datetime | None] = _ts(nullable=True)
    exchanged_at: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts(default=True)


class WebSession(Base):
    """A signed-in browser of the admin web, behind an HttpOnly cookie."""

    __tablename__ = "web_sessions"
    __table_args__ = (
        _tenant_unique("web_sessions"),
        _tenant_fk(["membership_id"], "memberships"),
        _tenant_fk(["invitation_id"], "invitations"),
        UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint(_in("revoke_reason", *SESSION_REVOKE_REASONS), name="revoke_reason"),
        CheckConstraint("(revoked_at IS NULL) = (revoke_reason IS NULL)", name="revoked"),
        CheckConstraint(
            "(sign_in_request_id IS NULL) <> (invitation_id IS NULL)", name="opened_by_one"
        ),
        Index(
            "ix_web_sessions_membership_id_live",
            "membership_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    membership_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    sign_in_request_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sign_in_requests.id")
    )
    invitation_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _ts(default=True)
    last_seen_at: Mapped[datetime] = _ts(default=True)
    expires_at: Mapped[datetime] = _ts()
    revoked_at: Mapped[datetime | None] = _ts(nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)


class Device(Base):
    """A Mac: the app's device token and the Mac's own Valkey user (DECISIONS #7)."""

    __tablename__ = "devices"
    __table_args__ = (
        _tenant_unique("devices"),
        _tenant_fk(["membership_id"], "memberships"),
        UniqueConstraint("sign_in_request_id", name="uq_devices_sign_in_request_id"),
        UniqueConstraint("token_hash", name="uq_devices_token_hash"),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint(_in("state", *DEVICE_STATES), name="state"),
        CheckConstraint(_in("revoke_reason", *DEVICE_REVOKE_REASONS), name="revoke_reason"),
        CheckConstraint(
            "(state = 'revoked') = (revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="revoked",
        ),
        CheckConstraint(
            "state <> 'active' OR valkey_password_sha256 IS NOT NULL", name="active_password"
        ),
        CheckConstraint(
            "valkey_password_sha256 IS NULL OR octet_length(valkey_password_sha256) = 32",
            name="valkey_password_sha256_length",
        ),
        Index(
            "ix_devices_organization_id_user_id_live",
            "organization_id",
            "user_id",
            postgresql_where=text("state <> 'revoked'"),
        ),
        Index(
            "ix_devices_organization_id_cleanup_owed",
            "organization_id",
            postgresql_where=text("state = 'revoked' AND valkey_user_deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    membership_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    sign_in_request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sign_in_requests.id")
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    valkey_username: Mapped[str] = mapped_column(
        Text, Computed("'floppy_d_' || replace(id::text, '-', '')", persisted=True)
    )
    valkey_password_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    valkey_user_created_at: Mapped[datetime | None] = _ts(nullable=True)
    valkey_user_deleted_at: Mapped[datetime | None] = _ts(nullable=True)
    activated_at: Mapped[datetime | None] = _ts(nullable=True)
    revoked_at: Mapped[datetime | None] = _ts(nullable=True)
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = _ts(nullable=True)


class DeviceStatus(Base):
    """What a Mac last reported in its heartbeat. One row per device, overwritten."""

    __tablename__ = "device_status"
    __table_args__ = (
        _tenant_fk(["device_id"], "devices"),
        CheckConstraint(_in("replica_mode", *REPLICA_MODES), name="replica_mode"),
        Index(
            "ix_device_status_organization_id_last_seen_at",
            "organization_id",
            text("last_seen_at DESC"),
        ),
        Index("ix_device_status_mounted_volume_ids", "mounted_volume_ids", postgresql_using="gin"),
        {"postgresql_with": {"fillfactor": 70}},
    )

    device_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    last_seen_at: Mapped[datetime] = _ts()
    app_version: Mapped[str] = mapped_column(Text)
    macos_version: Mapped[str] = mapped_column(Text)
    replica_mode: Mapped[str | None] = mapped_column(Text)
    replica_lag_ms: Mapped[int | None] = mapped_column(Integer)
    s3_access_key_tail: Mapped[str | None] = mapped_column(Text)
    mounted_volume_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), server_default=text("'{}'")
    )
    outdated_logged_version: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )


class S3Key(Base):
    """One of the organization's S3 keys, one row per key ever pasted (DECISIONS #4)."""

    __tablename__ = "s3_keys"
    __table_args__ = (
        _tenant_unique("s3_keys"),
        CheckConstraint(_in("state", *KEY_STATES), name="state"),
        CheckConstraint(
            "(state IN ('retired', 'rejected')) = (secret_key_enc IS NULL)", name="secret_wiped"
        ),
        CheckConstraint("length(access_key_id) BETWEEN 1 AND 256", name="access_key_id_length"),
        Index(
            "uq_s3_keys_organization_id_active",
            "organization_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index(
            "uq_s3_keys_organization_id_access_key_id_live",
            "organization_id",
            "access_key_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'active', 'retiring')"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    access_key_id: Mapped[str] = mapped_column(Text)
    access_key_tail: Mapped[str] = mapped_column(
        Text, Computed("right(access_key_id, 5)", persisted=True)
    )
    secret_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = _ts(default=True)
    activated_at: Mapped[datetime | None] = _ts(nullable=True)
    retiring_at: Mapped[datetime | None] = _ts(nullable=True)
    retired_at: Mapped[datetime | None] = _ts(nullable=True)
    rejected_at: Mapped[datetime | None] = _ts(nullable=True)


class Volume(Base):
    """A disk: a JuiceFS volume, a prefix in the bucket and a database on Valkey."""

    __tablename__ = "volumes"
    __table_args__ = (
        _tenant_unique("volumes"),
        _tenant_fk(["s3_key_id"], "s3_keys"),
        # The folder in /Volumes/Floppy (DATABASE_SCHEMA.md, "Disks on a Mac"): ASCII only, so
        # no Unicode normalization can change a path CapCut keeps.
        CheckConstraint(
            "name ~ '^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,58}[A-Za-z0-9])?$'", name="name_format"
        ),
        CheckConstraint("(state = 'failed') OR format_error IS NULL", name="format_error"),
        CheckConstraint(
            "volume_name ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="volume_name_format"
        ),
        CheckConstraint("meta_db >= 1", name="meta_db_positive"),
        CheckConstraint(_in("state", *VOLUME_STATES), name="state"),
        CheckConstraint("block_size_kb > 0", name="block_size_positive"),
        CheckConstraint("trash_days BETWEEN 0 AND 365", name="trash_days_range"),
        CheckConstraint("cache_expire_days BETWEEN 1 AND 365", name="cache_expire_days_range"),
        CheckConstraint(
            "state <> 'ready' OR (s3_key_id IS NOT NULL AND juicefs_uuid IS NOT NULL)", name="ready"
        ),
        Index(
            "uq_volumes_organization_id_name",
            "organization_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        UniqueConstraint(
            "organization_id", "volume_name", name="uq_volumes_organization_id_volume_name"
        ),
        UniqueConstraint("organization_id", "meta_db", name="uq_volumes_organization_id_meta_db"),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(Text)
    volume_name: Mapped[str] = mapped_column(Text)
    s3_endpoint: Mapped[str] = mapped_column(Text)
    s3_region: Mapped[str] = mapped_column(Text)
    s3_bucket: Mapped[str] = mapped_column(Text)
    meta_db: Mapped[int] = mapped_column(Integer)
    juicefs_uuid: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    s3_key_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text, server_default=text("'creating'"))
    block_size_kb: Mapped[int] = mapped_column(Integer, server_default=text("1024"))
    trash_days: Mapped[int] = mapped_column(Integer, server_default=text("7"))
    cache_expire_days: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    format_error: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts(default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = _ts(nullable=True)


class VolumeStatus(Base):
    """What the control plane last measured of a disk. One row per volume, overwritten."""

    __tablename__ = "volume_status"
    __table_args__ = (_tenant_fk(["volume_id"], "volumes"),)

    volume_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    reachable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    checked_at: Mapped[datetime | None] = _ts(nullable=True)
    used_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    file_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    trash_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_write_at: Mapped[datetime | None] = _ts(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=NOW, onupdate=func.now()
    )


class KeyRotation(Base):
    """A key rotation; the row is also the worker's job."""

    __tablename__ = "key_rotations"
    __table_args__ = (
        _tenant_unique("key_rotations"),
        _tenant_fk(["old_key_id"], "s3_keys", name="fk_key_rotations_old_key_id_s3_keys"),
        _tenant_fk(["new_key_id"], "s3_keys", name="fk_key_rotations_new_key_id_s3_keys"),
        CheckConstraint("old_key_id <> new_key_id", name="keys_differ"),
        CheckConstraint(_in("state", *ROTATION_STATES), name="state"),
        CheckConstraint(_in("probe_state", *STEP_STATES), name="probe_state"),
        CheckConstraint("macs_total >= 0", name="macs_total_positive"),
        Index(
            "uq_key_rotations_organization_id_applying",
            "organization_id",
            unique=True,
            postgresql_where=text("state = 'applying'"),
        ),
        Index(
            "uq_key_rotations_organization_id_awaiting",
            "organization_id",
            unique=True,
            postgresql_where=text("state = 'awaiting_old_key_deletion'"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    old_key_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    new_key_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text, server_default=text("'applying'"))
    probe_state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    probe_error_code: Mapped[str | None] = mapped_column(Text)
    probe_error_request: Mapped[str | None] = mapped_column(Text)
    macs_total: Mapped[int] = mapped_column(Integer)
    started_by_user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    started_at: Mapped[datetime] = _ts(default=True)
    applied_at: Mapped[datetime | None] = _ts(nullable=True)
    failed_at: Mapped[datetime | None] = _ts(nullable=True)
    superseded_at: Mapped[datetime | None] = _ts(nullable=True)
    closed_at: Mapped[datetime | None] = _ts(nullable=True)
    closed_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )


class KeyRotationVolume(Base):
    """One step of a rotation: `juicefs config` on one disk."""

    __tablename__ = "key_rotation_volumes"
    __table_args__ = (
        _tenant_fk(["rotation_id"], "key_rotations"),
        _tenant_fk(["volume_id"], "volumes"),
        CheckConstraint(_in("state", *STEP_STATES), name="state"),
    )

    rotation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    volume_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    state: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = _ts(nullable=True)
    finished_at: Mapped[datetime | None] = _ts(nullable=True)


class KeyExposure(Base):
    """Why the key window is open: someone who could read the key no longer has access."""

    __tablename__ = "key_exposures"
    __table_args__ = (
        _tenant_unique("key_exposures"),
        _tenant_fk(["membership_id"], "memberships"),
        _tenant_fk(["device_id"], "devices"),
        _tenant_fk(["closed_by_rotation_id"], "key_rotations"),
        CheckConstraint(_in("kind", *EXPOSURE_KINDS), name="kind"),
        CheckConstraint(
            "(kind = 'person_removed') = (membership_id IS NOT NULL)", name="person_removed"
        ),
        CheckConstraint("(kind = 'mac_revoked') = (device_id IS NOT NULL)", name="mac_revoked"),
        CheckConstraint("(closed_at IS NULL) = (closed_by_rotation_id IS NULL)", name="closed"),
        Index(
            "ix_key_exposures_organization_id_open",
            "organization_id",
            postgresql_where=text("closed_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    kind: Mapped[str] = mapped_column(Text)
    membership_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    device_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    label: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = _ts(default=True)
    closed_at: Mapped[datetime | None] = _ts(nullable=True)
    closed_by_rotation_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))


class AuditEvent(Base):
    """One audit log row; the table is append only (grants and a trigger)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        _tenant_unique("audit_events"),
        CheckConstraint(_in("actor_kind", *ACTOR_KINDS), name="actor_kind"),
        CheckConstraint(
            "(actor_kind = 'person') = (actor_user_id IS NOT NULL)", name="person_actor"
        ),
        CheckConstraint(_in("action", *AUDIT_ACTIONS), name="action"),
        CheckConstraint(_in("category", *AUDIT_CATEGORIES), name="category"),
        CheckConstraint(_in("target_kind", *AUDIT_TARGETS), name="target_kind"),
        CheckConstraint("jsonb_typeof(detail) = 'object'", name="detail_object"),
        Index(
            "ix_audit_events_organization_id_occurred_at_id",
            "organization_id",
            text("occurred_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_audit_events_organization_id_category_occurred_at",
            "organization_id",
            "category",
            text("occurred_at DESC"),
        ),
    )

    id: Mapped[UUID] = _id()
    organization_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id")
    )
    occurred_at: Mapped[datetime] = _ts(default=True)
    actor_kind: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    actor_label: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    target_label: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


async def lock_organization(session: AsyncSession, organization_id: UUID) -> None:
    """
    Takes the organization's row lock for organization-level invariants: the last admin,
    one rotation at a time, allocating a disk's Valkey database.
    """
    await session.execute(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )


async def advisory_lock(db: AsyncSession, job: str, organization_id: UUID, *, wait: bool) -> bool:
    """
    A transaction-level advisory lock for one job on one organization (the ACL reconciler and
    Mac registration share `acl`). With `wait=False`, returns whether it was taken.
    """
    key = func.hashtextextended(f"{job}:{organization_id}", 0)
    if wait:
        await db.execute(select(func.pg_advisory_xact_lock(key)))
        return True
    return bool(await db.scalar(select(func.pg_try_advisory_xact_lock(key))))
