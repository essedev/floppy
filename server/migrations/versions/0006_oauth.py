"""
MCP clients' authorization (docs/DATABASE_SCHEMA.md, "OAuth"; DECISIONS #29): the clients the
authorization server knows, the authorization requests with their codes, and the tokens by
SHA-256; two audit actions for a client connected and disconnected.

The application role gets SELECT, INSERT and UPDATE: a code is spent and a token revoked by
a timestamp, never deleted.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("FLOPPY_DB_APP_ROLE", "floppy_app")
TABLES = ("oauth_clients", "oauth_authorizations", "oauth_tokens")

ACTIONS_0005 = (
    "organization_created",
    "signed_in",
    "sign_in_locked",
    "person_invited",
    "invite_resent",
    "invite_accepted",
    "role_changed",
    "person_removed",
    "device_registered",
    "device_revoked",
    "device_signed_out",
    "device_seen_outdated",
    "key_rotation_started",
    "key_rotated",
    "key_rotation_failed",
    "key_window_closed",
    "disk_created",
    "disk_updated",
    "settings_updated",
    "key_created",
    "key_deleted",
    "disk_access_changed",
    "disk_deleted",
)
ACTIONS_0006 = (*ACTIONS_0005, "mcp_client_connected", "mcp_client_disconnected")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _grant() -> None:
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE})
        .scalar_one_or_none()
    )
    if exists is None:
        print(
            f"warning: role {APP_ROLE} does not exist; grants skipped (development database with one role)"
        )
        return
    role = op.get_bind().dialect.identifier_preparer.quote(APP_ROLE)
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {role}")


def upgrade() -> None:
    """Creates the three OAuth tables with their grants, and adds the two audit actions."""
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fresh_until", ts, nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", ts, nullable=True),
        sa.CheckConstraint(_in("source", ("dcr", "cimd")), name=op.f("ck_oauth_clients_source")),
        sa.CheckConstraint(
            "length(client_id) BETWEEN 1 AND 512", name=op.f("ck_oauth_clients_client_id_length")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(info) = 'object'", name=op.f("ck_oauth_clients_info_object")
        ),
        sa.CheckConstraint(
            "source = 'cimd' OR fresh_until IS NULL",
            name=op.f("ck_oauth_clients_fresh_until_cimd"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_clients")),
    )
    op.create_index(
        "uq_oauth_clients_client_id",
        "oauth_clients",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "oauth_authorizations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_explicit", sa.Boolean(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("membership_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("code_hash", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", ts, nullable=False),
        sa.Column("approved_at", ts, nullable=True),
        sa.Column("denied_at", ts, nullable=True),
        sa.Column("consumed_at", ts, nullable=True),
        sa.CheckConstraint(
            "code_hash IS NULL OR octet_length(code_hash) = 32",
            name=op.f("ck_oauth_authorizations_code_hash_length"),
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR denied_at IS NULL",
            name=op.f("ck_oauth_authorizations_decided_once"),
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL) = (code_hash IS NULL)",
            name=op.f("ck_oauth_authorizations_code_on_approval"),
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL) = (membership_id IS NULL)",
            name=op.f("ck_oauth_authorizations_person_on_approval"),
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR approved_at IS NOT NULL",
            name=op.f("ck_oauth_authorizations_consumed_approved"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name=op.f("fk_oauth_authorizations_organization_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_oauth_authorizations_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_oauth_authorizations_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_authorizations")),
        sa.UniqueConstraint("code_hash", name="uq_oauth_authorizations_code_hash"),
    )

    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("grant_id", sa.UUID(), nullable=False),
        sa.Column("authorization_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("granted_at", ts, nullable=False),
        sa.Column("last_used_at", ts, nullable=True),
        sa.Column("expires_at", ts, nullable=False),
        sa.Column("revoked_at", ts, nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32", name=op.f("ck_oauth_tokens_token_hash_length")
        ),
        sa.CheckConstraint(_in("kind", ("access", "refresh")), name=op.f("ck_oauth_tokens_kind")),
        sa.CheckConstraint(
            _in("revoke_reason", ("refreshed", "revoked", "code_replayed", "membership_removed")),
            name=op.f("ck_oauth_tokens_revoke_reason"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)", name=op.f("ck_oauth_tokens_revoked")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name=op.f("fk_oauth_tokens_organization_id_memberships"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_oauth_tokens_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_oauth_tokens_user_id_users")),
        sa.ForeignKeyConstraint(
            ["authorization_id"],
            ["oauth_authorizations.id"],
            name=op.f("fk_oauth_tokens_authorization_id_oauth_authorizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_tokens")),
        sa.UniqueConstraint("organization_id", "id", name="uq_oauth_tokens_organization_id_id"),
        sa.UniqueConstraint("token_hash", name="uq_oauth_tokens_token_hash"),
    )
    op.create_index("ix_oauth_tokens_grant_id", "oauth_tokens", ["grant_id"])
    op.create_index("ix_oauth_tokens_authorization_id", "oauth_tokens", ["authorization_id"])
    op.create_index(
        "ix_oauth_tokens_membership_id_live",
        "oauth_tokens",
        ["membership_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.drop_constraint(op.f("ck_audit_events_action"), "audit_events", type_="check")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0006))
    _grant()


def downgrade() -> None:
    """Refuses while an MCP audit event exists: the audit log is never rewritten."""
    count = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT count(*) FROM audit_events WHERE action IN "
            "('mcp_client_connected', 'mcp_client_disconnected')"
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError(f"migration 0006 downgrade: the audit log holds {count} MCP event(s)")
    op.drop_constraint(op.f("ck_audit_events_action"), "audit_events", type_="check")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0005))
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_authorizations")
    op.drop_table("oauth_clients")
