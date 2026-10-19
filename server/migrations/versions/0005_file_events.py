"""
The disks' activity (docs/DATABASE_SCHEMA.md, "file_events"; DECISIONS #27): what each device
did on the disks it sees, kept 30 days, and what it is writing now.

`file_events` is the one table the application role may delete from: its rows are purged
after 30 days by the control plane itself, which has no owner connection at run time.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("FLOPPY_DB_APP_ROLE", "floppy_app")
FILE_OPS = ("created", "added", "written", "replaced", "renamed", "deleted")
ENTRY_KINDS = ("file", "folder")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _tenant_fk(table: str, column: str, referred: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", column],
        [f"{referred}.organization_id", f"{referred}.id"],
        name=op.f(f"fk_{table}_organization_id_{referred}"),
    )


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organizations.id"],
        name=op.f(f"fk_{table}_organization_id_organizations"),
    )


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
    op.execute(f"REVOKE ALL ON file_events FROM {role}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON file_events TO {role}")
    op.execute(f"REVOKE ALL ON device_activity FROM {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON device_activity TO {role}")


def upgrade() -> None:
    """Creates `file_events` and `device_activity` with their grants."""
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "file_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("local_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", ts, nullable=False),
        sa.Column("received_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("dest", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(_in("op", FILE_OPS), name=op.f("ck_file_events_op")),
        sa.CheckConstraint(_in("kind", ENTRY_KINDS), name=op.f("ck_file_events_kind")),
        sa.CheckConstraint(
            "(op = 'renamed') = (dest IS NOT NULL)", name=op.f("ck_file_events_dest")
        ),
        sa.CheckConstraint(
            "length(path) BETWEEN 1 AND 4096", name=op.f("ck_file_events_path_length")
        ),
        sa.CheckConstraint(
            "dest IS NULL OR length(dest) BETWEEN 1 AND 4096",
            name=op.f("ck_file_events_dest_length"),
        ),
        sa.CheckConstraint("size IS NULL OR size >= 0", name=op.f("ck_file_events_size")),
        sa.CheckConstraint("local_id > 0", name=op.f("ck_file_events_local_id")),
        _tenant_fk("file_events", "volume_id", "volumes"),
        _tenant_fk("file_events", "device_id", "devices"),
        _org_fk("file_events"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_file_events_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_events")),
        sa.UniqueConstraint("organization_id", "id", name="uq_file_events_organization_id_id"),
        sa.UniqueConstraint("device_id", "local_id", name="uq_file_events_device_id_local_id"),
    )
    op.create_index(
        "ix_file_events_organization_id_received_at_id",
        "file_events",
        ["organization_id", "received_at", "id"],
    )
    op.create_index(
        "ix_file_events_volume_id_occurred_at_id",
        "file_events",
        ["volume_id", sa.text("occurred_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_file_events_volume_id_path_occurred_at",
        "file_events",
        ["volume_id", "path", "occurred_at"],
    )
    op.create_table(
        "device_activity",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "writing",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reported_at", ts, nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(writing) = 'array'", name=op.f("ck_device_activity_writing_array")
        ),
        _tenant_fk("device_activity", "device_id", "devices"),
        _org_fk("device_activity"),
        sa.PrimaryKeyConstraint("device_id", name=op.f("pk_device_activity")),
        postgresql_with={"fillfactor": 70},
    )
    op.create_index(
        "ix_device_activity_organization_id_reported_at",
        "device_activity",
        ["organization_id", "reported_at"],
    )
    _grant()


def downgrade() -> None:
    """Drops both tables: the activity is an observation, nothing else depends on it."""
    op.drop_table("device_activity")
    op.drop_table("file_events")
