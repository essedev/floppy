"""
The whole control plane schema (docs/DATABASE_SCHEMA.md, "First migration").

Written from autogenerate and edited by hand for what autogenerate does not do: the server
version check, the append-only trigger on the audit log and the grants of the application
role, which gets no DELETE or TRUNCATE anywhere and only SELECT and INSERT on the audit log.

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("FLOPPY_DB_APP_ROLE", "floppy_app")

TABLES = (
    "organizations",
    "organization_status",
    "users",
    "memberships",
    "invitations",
    "sign_in_requests",
    "web_sessions",
    "devices",
    "device_status",
    "s3_keys",
    "volumes",
    "volume_status",
    "key_rotations",
    "key_rotation_volumes",
    "key_exposures",
    "audit_events",
)


def _check_server() -> None:
    # uuidv7() arrived in PostgreSQL 18; on an older server every insert would fail.
    version = op.get_bind().execute(sa.text("SHOW server_version_num")).scalar_one()
    if int(version) < 180000:
        raise RuntimeError(f"PostgreSQL 18 or newer is required (server_version_num {version})")


def _append_only_audit() -> None:
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          -- The owner keeps the one documented exception (erasing a person, retention).
          IF current_user = (SELECT tableowner FROM pg_tables
                             WHERE schemaname = TG_TABLE_SCHEMA AND tablename = TG_TABLE_NAME) THEN
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
          END IF;
          RAISE EXCEPTION 'audit_events is append only: % refused', TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
    )


def _grant_app_role() -> None:
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE})
        .scalar_one_or_none()
    )
    if exists is None:
        print(f"warning: role {APP_ROLE} does not exist; grants skipped (development database with one role)")
        return
    role = op.get_bind().dialect.identifier_preparer.quote(APP_ROLE)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    for table in TABLES:
        privileges = "SELECT, INSERT" if table == "audit_events" else "SELECT, INSERT, UPDATE"
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"GRANT {privileges} ON {table} TO {role}")


def upgrade() -> None:
    """Creates every table, index, the audit trigger and the grants."""
    _check_server()
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("s3_endpoint", sa.Text(), nullable=False),
        sa.Column("s3_region", sa.Text(), nullable=False),
        sa.Column("s3_bucket", sa.Text(), nullable=False),
        sa.Column("valkey_url", sa.Text(), nullable=False),
        sa.Column("valkey_admin_url", sa.Text(), nullable=True),
        sa.Column("valkey_admin_username", sa.Text(), nullable=True),
        sa.Column("valkey_admin_password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("valkey_databases", sa.Integer(), server_default=sa.text("16"), nullable=False),
        sa.Column("smtp_host", sa.Text(), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_username", sa.Text(), nullable=True),
        sa.Column("smtp_sender", sa.Text(), nullable=True),
        sa.Column("smtp_password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "position('@' in valkey_url) = 0", name=op.f("ck_organizations_valkey_url_no_credentials")
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'", name=op.f("ck_organizations_slug_format")
        ),
        sa.CheckConstraint(
            "(smtp_host IS NULL) = (smtp_port IS NULL)", name=op.f("ck_organizations_smtp_host_port")
        ),
        sa.CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 100", name=op.f("ck_organizations_name_length")
        ),
        sa.CheckConstraint("smtp_port BETWEEN 1 AND 65535", name=op.f("ck_organizations_smtp_port_range")),
        sa.CheckConstraint(
            "valkey_databases BETWEEN 2 AND 1024", name=op.f("ck_organizations_valkey_databases_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index(
        "uq_organizations_s3_endpoint_s3_bucket",
        "organizations",
        ["s3_endpoint", "s3_bucket"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_organizations_valkey_url",
        "organizations",
        [sa.literal_column("lower(valkey_url)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "email = lower(btrim(email)) AND position('@' in email) > 1",
            name=op.f("ck_users_email_normalized"),
        ),
        sa.CheckConstraint("length(name) <= 100", name=op.f("ck_users_name_length")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email", "users", ["email"], unique=True, postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_label", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("target_label", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(actor_kind = 'person') = (actor_user_id IS NOT NULL)", name=op.f("ck_audit_events_person_actor")
        ),
        sa.CheckConstraint(
            "action IN ('signed_in', 'sign_in_locked', 'person_invited', 'invite_resent', 'invite_accepted', 'role_changed', 'person_removed', 'mac_registered', 'mac_revoked', 'mac_signed_out', 'mac_seen_outdated', 'key_rotation_started', 'key_rotated', 'key_rotation_failed', 'key_window_closed', 'disk_created', 'disk_updated', 'settings_updated')",
            name=op.f("ck_audit_events_action"),
        ),
        sa.CheckConstraint(
            "actor_kind IN ('person', 'system', 'operator')", name=op.f("ck_audit_events_actor_kind")
        ),
        sa.CheckConstraint(
            "category IN ('access', 'people', 'macs', 'key', 'disks', 'settings')",
            name=op.f("ck_audit_events_category"),
        ),
        sa.CheckConstraint("jsonb_typeof(detail) = 'object'", name=op.f("ck_audit_events_detail_object")),
        sa.CheckConstraint(
            "target_kind IN ('person', 'mac', 'disk', 'key', 'organization')",
            name=op.f("ck_audit_events_target_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_events_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_events_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("organization_id", "id", name="uq_audit_events_organization_id_id"),
    )
    op.create_index(
        "ix_audit_events_organization_id_category_occurred_at",
        "audit_events",
        ["organization_id", "category", sa.literal_column("occurred_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_organization_id_occurred_at_id",
        "audit_events",
        ["organization_id", sa.literal_column("occurred_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'invited'"), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'removed') = (removed_at IS NOT NULL)", name=op.f("ck_memberships_removed_at")
        ),
        sa.CheckConstraint("role IN ('admin', 'member')", name=op.f("ck_memberships_role")),
        sa.CheckConstraint(
            "status <> 'active' OR activated_at IS NOT NULL", name=op.f("ck_memberships_active_activated")
        ),
        sa.CheckConstraint(
            "status <> 'invited' OR activated_at IS NULL", name=op.f("ck_memberships_invited_not_activated")
        ),
        sa.CheckConstraint("status IN ('invited', 'active', 'removed')", name=op.f("ck_memberships_status")),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], name=op.f("fk_memberships_invited_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_memberships_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["removed_by_user_id"], ["users.id"], name=op.f("fk_memberships_removed_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("organization_id", "id", name="uq_memberships_organization_id_id"),
    )
    op.create_index(
        "ix_memberships_organization_id_status", "memberships", ["organization_id", "status"], unique=False
    )
    op.create_index(
        "ix_memberships_user_id_live",
        "memberships",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("status <> 'removed' AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_memberships_organization_id_user_id",
        "memberships",
        ["organization_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'removed' AND deleted_at IS NULL"),
    )
    op.create_table(
        "organization_status",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("metadata_state", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("metadata_latency_ms", sa.Integer(), nullable=True),
        sa.Column("metadata_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_state", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("storage_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mail_state", sa.Text(), server_default=sa.text("'unconfigured'"), nullable=False),
        sa.Column("mail_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "mail_state IN ('ok', 'unconfigured', 'failing')", name=op.f("ck_organization_status_mail_state")
        ),
        sa.CheckConstraint(
            "metadata_state IN ('ok', 'down')", name=op.f("ck_organization_status_metadata_state")
        ),
        sa.CheckConstraint(
            "storage_state IN ('ok', 'failing')", name=op.f("ck_organization_status_storage_state")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_status_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("organization_id", name=op.f("pk_organization_status")),
    )
    op.create_table(
        "s3_keys",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("access_key_id", sa.Text(), nullable=False),
        sa.Column(
            "access_key_tail",
            sa.Text(),
            sa.Computed("right(access_key_id, 5)", persisted=True),
            nullable=False,
        ),
        sa.Column("secret_key_enc", sa.LargeBinary(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retiring_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(state IN ('retired', 'rejected')) = (secret_key_enc IS NULL)",
            name=op.f("ck_s3_keys_secret_wiped"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'retiring', 'retired', 'rejected')", name=op.f("ck_s3_keys_state")
        ),
        sa.CheckConstraint(
            "length(access_key_id) BETWEEN 1 AND 256", name=op.f("ck_s3_keys_access_key_id_length")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f("fk_s3_keys_created_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_s3_keys_organization_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_s3_keys")),
        sa.UniqueConstraint("organization_id", "id", name="uq_s3_keys_organization_id_id"),
    )
    op.create_index(
        "uq_s3_keys_organization_id_access_key_id_live",
        "s3_keys",
        ["organization_id", "access_key_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'active', 'retiring')"),
    )
    op.create_index(
        "uq_s3_keys_organization_id_active",
        "s3_keys",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "sign_in_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("link_token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("code_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("code_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resend_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_via", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_name", sa.Text(), nullable=True),
        sa.Column("code_challenge", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.Text(), nullable=True),
        sa.Column("auth_code_hash", sa.LargeBinary(), nullable=True),
        sa.Column("auth_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(kind = 'app') = (code_challenge IS NOT NULL AND redirect_uri IS NOT NULL AND device_name IS NOT NULL)",
            name=op.f("ck_sign_in_requests_app_fields"),
        ),
        sa.CheckConstraint(
            "(state = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_sign_in_requests_completed_at"),
        ),
        sa.CheckConstraint(
            "(state = 'locked') = (locked_at IS NOT NULL)", name=op.f("ck_sign_in_requests_locked_at")
        ),
        sa.CheckConstraint(
            "(state = 'superseded') = (superseded_at IS NOT NULL)",
            name=op.f("ck_sign_in_requests_superseded_at"),
        ),
        sa.CheckConstraint(
            "completed_via IN ('link', 'code')", name=op.f("ck_sign_in_requests_completed_via")
        ),
        sa.CheckConstraint("kind IN ('web', 'app')", name=op.f("ck_sign_in_requests_kind")),
        sa.CheckConstraint(
            "redirect_uri ~ '^http://127\\.0\\.0\\.1:[0-9]{1,5}/'",
            name=op.f("ck_sign_in_requests_redirect_uri_loopback"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'completed', 'locked', 'superseded')",
            name=op.f("ck_sign_in_requests_state"),
        ),
        sa.CheckConstraint(
            "code_attempts BETWEEN 0 AND 5", name=op.f("ck_sign_in_requests_code_attempts_range")
        ),
        sa.CheckConstraint("email = lower(btrim(email))", name=op.f("ck_sign_in_requests_email_normalized")),
        sa.CheckConstraint(
            "octet_length(link_token_hash) = 32", name=op.f("ck_sign_in_requests_link_token_hash_length")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_sign_in_requests_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sign_in_requests_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sign_in_requests")),
        sa.UniqueConstraint("auth_code_hash", name="uq_sign_in_requests_auth_code_hash"),
        sa.UniqueConstraint("code_challenge", name="uq_sign_in_requests_code_challenge"),
        sa.UniqueConstraint("link_token_hash", name="uq_sign_in_requests_link_token_hash"),
    )
    op.create_index(
        "ix_sign_in_requests_email_created_at",
        "sign_in_requests",
        ["email", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("sign_in_request_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "valkey_username",
            sa.Text(),
            sa.Computed("'floppy_d_' || replace(id::text, '-', '')", persisted=True),
            nullable=False,
        ),
        sa.Column("valkey_password_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("valkey_user_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valkey_user_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.UUID(), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(state = 'revoked') = (revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name=op.f("ck_devices_revoked"),
        ),
        sa.CheckConstraint(
            "revoke_reason IN ('admin', 'owner', 'member_removed', 'signed_out', 'never_registered')",
            name=op.f("ck_devices_revoke_reason"),
        ),
        sa.CheckConstraint(
            "state <> 'active' OR valkey_password_sha256 IS NOT NULL", name=op.f("ck_devices_active_password")
        ),
        sa.CheckConstraint("state IN ('pending', 'active', 'revoked')", name=op.f("ck_devices_state")),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 100", name=op.f("ck_devices_name_length")),
        sa.CheckConstraint("octet_length(token_hash) = 32", name=op.f("ck_devices_token_hash_length")),
        sa.CheckConstraint(
            "valkey_password_sha256 IS NULL OR octet_length(valkey_password_sha256) = 32",
            name=op.f("ck_devices_valkey_password_sha256_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name=op.f("fk_devices_organization_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_devices_organization_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], name=op.f("fk_devices_revoked_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["sign_in_request_id"],
            ["sign_in_requests.id"],
            name=op.f("fk_devices_sign_in_request_id_sign_in_requests"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_devices_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("organization_id", "id", name="uq_devices_organization_id_id"),
        sa.UniqueConstraint("sign_in_request_id", name="uq_devices_sign_in_request_id"),
        sa.UniqueConstraint("token_hash", name="uq_devices_token_hash"),
    )
    op.create_index(
        "ix_devices_organization_id_cleanup_owed",
        "devices",
        ["organization_id"],
        unique=False,
        postgresql_where=sa.text("state = 'revoked' AND valkey_user_deleted_at IS NULL"),
    )
    op.create_index(
        "ix_devices_organization_id_user_id_live",
        "devices",
        ["organization_id", "user_id"],
        unique=False,
        postgresql_where=sa.text("state <> 'revoked'"),
    )
    op.create_table(
        "invitations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'sent'"), nullable=False),
        sa.Column("sent_by_user_id", sa.UUID(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(state = 'accepted') = (accepted_at IS NOT NULL)", name=op.f("ck_invitations_accepted_at")
        ),
        sa.CheckConstraint(
            "(state = 'revoked') = (revoked_at IS NOT NULL)", name=op.f("ck_invitations_revoked_at")
        ),
        sa.CheckConstraint(
            "(state = 'superseded') = (superseded_at IS NOT NULL)", name=op.f("ck_invitations_superseded_at")
        ),
        sa.CheckConstraint(
            "state IN ('sent', 'accepted', 'superseded', 'revoked')", name=op.f("ck_invitations_state")
        ),
        sa.CheckConstraint("expires_at > sent_at", name=op.f("ck_invitations_expires_after_sent")),
        sa.CheckConstraint("octet_length(token_hash) = 32", name=op.f("ck_invitations_token_hash_length")),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name=op.f("fk_invitations_organization_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_invitations_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["sent_by_user_id"], ["users.id"], name=op.f("fk_invitations_sent_by_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("organization_id", "id", name="uq_invitations_organization_id_id"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index(
        "uq_invitations_membership_id_sent",
        "invitations",
        ["membership_id"],
        unique=True,
        postgresql_where=sa.text("state = 'sent'"),
    )
    op.create_table(
        "key_rotations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("old_key_id", sa.UUID(), nullable=False),
        sa.Column("new_key_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'applying'"), nullable=False),
        sa.Column("probe_state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("probe_error_code", sa.Text(), nullable=True),
        sa.Column("probe_error_request", sa.Text(), nullable=True),
        sa.Column("macs_total", sa.Integer(), nullable=False),
        sa.Column("started_by_user_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "probe_state IN ('pending', 'running', 'done', 'failed')",
            name=op.f("ck_key_rotations_probe_state"),
        ),
        sa.CheckConstraint(
            "state IN ('applying', 'awaiting_old_key_deletion', 'failed', 'superseded', 'closed')",
            name=op.f("ck_key_rotations_state"),
        ),
        sa.CheckConstraint("macs_total >= 0", name=op.f("ck_key_rotations_macs_total_positive")),
        sa.CheckConstraint("old_key_id <> new_key_id", name=op.f("ck_key_rotations_keys_differ")),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"], ["users.id"], name=op.f("fk_key_rotations_closed_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "new_key_id"],
            ["s3_keys.organization_id", "s3_keys.id"],
            name="fk_key_rotations_new_key_id_s3_keys",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "old_key_id"],
            ["s3_keys.organization_id", "s3_keys.id"],
            name="fk_key_rotations_old_key_id_s3_keys",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_key_rotations_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["started_by_user_id"], ["users.id"], name=op.f("fk_key_rotations_started_by_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_key_rotations")),
        sa.UniqueConstraint("organization_id", "id", name="uq_key_rotations_organization_id_id"),
    )
    op.create_index(
        "uq_key_rotations_organization_id_applying",
        "key_rotations",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("state = 'applying'"),
    )
    op.create_index(
        "uq_key_rotations_organization_id_awaiting",
        "key_rotations",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("state = 'awaiting_old_key_deletion'"),
    )
    op.create_table(
        "volumes",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("volume_name", sa.Text(), nullable=False),
        sa.Column("s3_endpoint", sa.Text(), nullable=False),
        sa.Column("s3_region", sa.Text(), nullable=False),
        sa.Column("s3_bucket", sa.Text(), nullable=False),
        sa.Column("meta_db", sa.Integer(), nullable=False),
        sa.Column("juicefs_uuid", sa.UUID(), nullable=True),
        sa.Column("s3_key_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'creating'"), nullable=False),
        sa.Column("block_size_kb", sa.Integer(), server_default=sa.text("1024"), nullable=False),
        sa.Column("trash_days", sa.Integer(), server_default=sa.text("7"), nullable=False),
        sa.Column("cache_expire_days", sa.Integer(), server_default=sa.text("30"), nullable=False),
        sa.Column("format_error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state <> 'ready' OR (s3_key_id IS NOT NULL AND juicefs_uuid IS NOT NULL)",
            name=op.f("ck_volumes_ready"),
        ),
        sa.CheckConstraint("state IN ('creating', 'ready', 'failed')", name=op.f("ck_volumes_state")),
        sa.CheckConstraint(
            "volume_name ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name=op.f("ck_volumes_volume_name_format")
        ),
        sa.CheckConstraint("block_size_kb > 0", name=op.f("ck_volumes_block_size_positive")),
        sa.CheckConstraint(
            "cache_expire_days BETWEEN 1 AND 365", name=op.f("ck_volumes_cache_expire_days_range")
        ),
        sa.CheckConstraint(
            "name ~ '^[A-Za-z0-9]([A-Za-z0-9 ._-]{0,58}[A-Za-z0-9])?$'", name=op.f("ck_volumes_name_format")
        ),
        sa.CheckConstraint("(state = 'failed') OR format_error IS NULL", name=op.f("ck_volumes_format_error")),
        sa.CheckConstraint("meta_db >= 1", name=op.f("ck_volumes_meta_db_positive")),
        sa.CheckConstraint("trash_days BETWEEN 0 AND 365", name=op.f("ck_volumes_trash_days_range")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name=op.f("fk_volumes_created_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "s3_key_id"],
            ["s3_keys.organization_id", "s3_keys.id"],
            name=op.f("fk_volumes_organization_id_s3_keys"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=op.f("fk_volumes_organization_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_volumes")),
        sa.UniqueConstraint("organization_id", "id", name="uq_volumes_organization_id_id"),
        sa.UniqueConstraint("organization_id", "meta_db", name="uq_volumes_organization_id_meta_db"),
        sa.UniqueConstraint("organization_id", "volume_name", name="uq_volumes_organization_id_volume_name"),
    )
    op.create_index(
        "uq_volumes_organization_id_name",
        "volumes",
        ["organization_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "device_status",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("app_version", sa.Text(), nullable=False),
        sa.Column("macos_version", sa.Text(), nullable=False),
        sa.Column("replica_mode", sa.Text(), nullable=True),
        sa.Column("replica_lag_ms", sa.Integer(), nullable=True),
        sa.Column("s3_access_key_tail", sa.Text(), nullable=True),
        sa.Column(
            "mounted_volume_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("outdated_logged_version", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "replica_mode IN ('replica', 'master', 'offline')", name=op.f("ck_device_status_replica_mode")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "device_id"],
            ["devices.organization_id", "devices.id"],
            name=op.f("fk_device_status_organization_id_devices"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_device_status_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("device_id", name=op.f("pk_device_status")),
        postgresql_with={"fillfactor": 70},
    )
    op.create_index(
        "ix_device_status_mounted_volume_ids",
        "device_status",
        ["mounted_volume_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_device_status_organization_id_last_seen_at",
        "device_status",
        ["organization_id", sa.literal_column("last_seen_at DESC")],
        unique=False,
    )
    op.create_table(
        "key_exposures",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_rotation_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "(kind = 'mac_revoked') = (device_id IS NOT NULL)", name=op.f("ck_key_exposures_mac_revoked")
        ),
        sa.CheckConstraint(
            "(kind = 'person_removed') = (membership_id IS NOT NULL)",
            name=op.f("ck_key_exposures_person_removed"),
        ),
        sa.CheckConstraint("kind IN ('person_removed', 'mac_revoked')", name=op.f("ck_key_exposures_kind")),
        sa.CheckConstraint(
            "(closed_at IS NULL) = (closed_by_rotation_id IS NULL)", name=op.f("ck_key_exposures_closed")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "closed_by_rotation_id"],
            ["key_rotations.organization_id", "key_rotations.id"],
            name=op.f("fk_key_exposures_organization_id_key_rotations"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "device_id"],
            ["devices.organization_id", "devices.id"],
            name=op.f("fk_key_exposures_organization_id_devices"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name=op.f("fk_key_exposures_organization_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_key_exposures_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_key_exposures")),
        sa.UniqueConstraint("organization_id", "id", name="uq_key_exposures_organization_id_id"),
    )
    op.create_index(
        "ix_key_exposures_organization_id_open",
        "key_exposures",
        ["organization_id"],
        unique=False,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_table(
        "key_rotation_volumes",
        sa.Column("rotation_id", sa.UUID(), nullable=False),
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'done', 'failed')", name=op.f("ck_key_rotation_volumes_state")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "rotation_id"],
            ["key_rotations.organization_id", "key_rotations.id"],
            name=op.f("fk_key_rotation_volumes_organization_id_key_rotations"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "volume_id"],
            ["volumes.organization_id", "volumes.id"],
            name=op.f("fk_key_rotation_volumes_organization_id_volumes"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_key_rotation_volumes_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("rotation_id", "volume_id", name=op.f("pk_key_rotation_volumes")),
    )
    op.create_table(
        "volume_status",
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("reachable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("file_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("trash_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_write_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "volume_id"],
            ["volumes.organization_id", "volumes.id"],
            name=op.f("fk_volume_status_organization_id_volumes"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_volume_status_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("volume_id", name=op.f("pk_volume_status")),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("sign_in_request_id", sa.UUID(), nullable=True),
        sa.Column("invitation_id", sa.UUID(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "revoke_reason IN ('signed_out', 'membership_removed')",
            name=op.f("ck_web_sessions_revoke_reason"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)", name=op.f("ck_web_sessions_revoked")
        ),
        sa.CheckConstraint(
            "(sign_in_request_id IS NULL) <> (invitation_id IS NULL)",
            name=op.f("ck_web_sessions_opened_by_one"),
        ),
        sa.CheckConstraint("octet_length(token_hash) = 32", name=op.f("ck_web_sessions_token_hash_length")),
        sa.ForeignKeyConstraint(
            ["organization_id", "invitation_id"],
            ["invitations.organization_id", "invitations.id"],
            name=op.f("fk_web_sessions_organization_id_invitations"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name=op.f("fk_web_sessions_organization_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_web_sessions_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["sign_in_request_id"],
            ["sign_in_requests.id"],
            name=op.f("fk_web_sessions_sign_in_request_id_sign_in_requests"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_web_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_sessions")),
        sa.UniqueConstraint("organization_id", "id", name="uq_web_sessions_organization_id_id"),
        sa.UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
    )
    op.create_index(
        "ix_web_sessions_membership_id_live",
        "web_sessions",
        ["membership_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    _append_only_audit()
    _grant_app_role()


def downgrade() -> None:
    """Drops everything, in reverse order. For development only."""
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    op.drop_index(
        "ix_web_sessions_membership_id_live",
        table_name="web_sessions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("web_sessions")
    op.drop_table("volume_status")
    op.drop_table("key_rotation_volumes")
    op.drop_index(
        "ix_key_exposures_organization_id_open",
        table_name="key_exposures",
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.drop_table("key_exposures")
    op.drop_index("ix_device_status_organization_id_last_seen_at", table_name="device_status")
    op.drop_index("ix_device_status_mounted_volume_ids", table_name="device_status", postgresql_using="gin")
    op.drop_table("device_status")
    op.drop_index(
        "uq_volumes_organization_id_name",
        table_name="volumes",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("volumes")
    op.drop_index(
        "uq_key_rotations_organization_id_awaiting",
        table_name="key_rotations",
        postgresql_where=sa.text("state = 'awaiting_old_key_deletion'"),
    )
    op.drop_index(
        "uq_key_rotations_organization_id_applying",
        table_name="key_rotations",
        postgresql_where=sa.text("state = 'applying'"),
    )
    op.drop_table("key_rotations")
    op.drop_index(
        "uq_invitations_membership_id_sent",
        table_name="invitations",
        postgresql_where=sa.text("state = 'sent'"),
    )
    op.drop_table("invitations")
    op.drop_index(
        "ix_devices_organization_id_user_id_live",
        table_name="devices",
        postgresql_where=sa.text("state <> 'revoked'"),
    )
    op.drop_index(
        "ix_devices_organization_id_cleanup_owed",
        table_name="devices",
        postgresql_where=sa.text("state = 'revoked' AND valkey_user_deleted_at IS NULL"),
    )
    op.drop_table("devices")
    op.drop_index("ix_sign_in_requests_email_created_at", table_name="sign_in_requests")
    op.drop_table("sign_in_requests")
    op.drop_index(
        "uq_s3_keys_organization_id_active",
        table_name="s3_keys",
        postgresql_where=sa.text("state = 'active'"),
    )
    op.drop_index(
        "uq_s3_keys_organization_id_access_key_id_live",
        table_name="s3_keys",
        postgresql_where=sa.text("state IN ('pending', 'active', 'retiring')"),
    )
    op.drop_table("s3_keys")
    op.drop_table("organization_status")
    op.drop_index(
        "uq_memberships_organization_id_user_id",
        table_name="memberships",
        postgresql_where=sa.text("status <> 'removed' AND deleted_at IS NULL"),
    )
    op.drop_index(
        "ix_memberships_user_id_live",
        table_name="memberships",
        postgresql_where=sa.text("status <> 'removed' AND deleted_at IS NULL"),
    )
    op.drop_index("ix_memberships_organization_id_status", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_audit_events_organization_id_occurred_at_id", table_name="audit_events")
    op.drop_index("ix_audit_events_organization_id_category_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("uq_users_email", table_name="users", postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_table("users")
    op.drop_index(
        "uq_organizations_valkey_url",
        table_name="organizations",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_organizations_s3_endpoint_s3_bucket",
        table_name="organizations",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("organizations")
