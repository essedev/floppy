"""
One Valkey instance and one bucket per disk, with access per person and per disk
(docs/DATABASE_SCHEMA.md, "Migration 0003"; DECISIONS #23, #24).

The organization's Valkey instance, its bucket, the team and person keys and the key scope
go; each disk gets its own instance (`meta_instances`), its bucket (on `volumes`), a key in
its format (manual) or per-device keys granted per bucket (managed), and the access of each
person (`disk_access`) with what is applied on each instance (`meta_users`) and at the
provider (`s3_key_grants`). Rotations and exposures become per disk.

It refuses a database that holds organizations, in both directions: an organization of 0002
has one instance and one bucket that this model splits per disk, and its keys' secrets are
sealed per row, out of a migration's reach. There is no production data.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("FLOPPY_DB_APP_ROLE", "floppy_app")
NEW_TABLES = ("meta_instances", "disk_access", "meta_users", "s3_key_grants")

ACTIONS_0002 = (
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
)
ACTIONS_0003 = (*ACTIONS_0002, "disk_access_changed")
ACCESS = ("none", "read", "write")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _refuse_organizations(direction: str) -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM organizations")).scalar_one()
    if count:
        raise RuntimeError(
            f"migration 0003 {direction}: this database holds {count} organization(s). Per-disk "
            "instances and buckets cannot be derived from one organization-wide instance, and "
            "stored secrets are sealed per row: recreate the database and run the bootstrap again "
            "(docs/DATABASE_SCHEMA.md, Migration 0003)."
        )


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


def _grant_new_tables() -> None:
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
    for table in NEW_TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {role}")


def _create_tables() -> None:
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "meta_instances",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("service_ref", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("admin_url", sa.Text(), nullable=True),
        sa.Column("admin_username", sa.Text(), nullable=True),
        sa.Column("admin_password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'ready'"), nullable=False),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("stopped_at", ts, nullable=True),
        sa.CheckConstraint(
            _in("state", ("ready", "stopped")), name=op.f("ck_meta_instances_state")
        ),
        sa.CheckConstraint(
            "position('@' in url) = 0", name=op.f("ck_meta_instances_url_no_credentials")
        ),
        sa.CheckConstraint(
            "position('@' in admin_url) = 0",
            name=op.f("ck_meta_instances_admin_url_no_credentials"),
        ),
        sa.CheckConstraint(
            "length(service_ref) BETWEEN 1 AND 200",
            name=op.f("ck_meta_instances_service_ref_length"),
        ),
        sa.CheckConstraint(
            "(state = 'stopped') = (stopped_at IS NOT NULL)",
            name=op.f("ck_meta_instances_stopped_at"),
        ),
        _tenant_fk("meta_instances", "volume_id", "volumes"),
        _org_fk("meta_instances"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meta_instances")),
        sa.UniqueConstraint("organization_id", "id", name="uq_meta_instances_organization_id_id"),
    )
    op.create_index(
        "uq_meta_instances_volume_id_ready",
        "meta_instances",
        ["volume_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ready'"),
    )
    op.create_index(
        "uq_meta_instances_url_ready",
        "meta_instances",
        [sa.text("lower(url)")],
        unique=True,
        postgresql_where=sa.text("state = 'ready'"),
    )

    op.create_table(
        "disk_access",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("set_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("access", ACCESS), name=op.f("ck_disk_access_access")),
        _tenant_fk("disk_access", "volume_id", "volumes"),
        _tenant_fk("disk_access", "membership_id", "memberships"),
        _org_fk("disk_access"),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"], ["users.id"], name=op.f("fk_disk_access_set_by_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disk_access")),
        sa.UniqueConstraint("organization_id", "id", name="uq_disk_access_organization_id_id"),
        sa.UniqueConstraint(
            "volume_id", "membership_id", name="uq_disk_access_volume_id_membership_id"
        ),
    )
    op.create_index(
        "ix_disk_access_organization_id_membership_id",
        "disk_access",
        ["organization_id", "membership_id"],
    )

    op.create_table(
        "meta_users",
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("applied_at", ts, nullable=True),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("access", ACCESS), name=op.f("ck_meta_users_access")),
        _tenant_fk("meta_users", "device_id", "devices"),
        _tenant_fk("meta_users", "volume_id", "volumes"),
        _org_fk("meta_users"),
        sa.PrimaryKeyConstraint("device_id", "volume_id", name=op.f("pk_meta_users")),
    )
    op.create_index(
        "ix_meta_users_organization_id_owed",
        "meta_users",
        ["organization_id"],
        postgresql_where=sa.text("applied_at IS NULL"),
    )

    op.create_table(
        "s3_key_grants",
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("volume_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("applied_at", ts, nullable=True),
        sa.Column("updated_at", ts, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("access", ACCESS), name=op.f("ck_s3_key_grants_access")),
        sa.ForeignKeyConstraint(
            ["organization_id", "key_id"],
            ["s3_keys.organization_id", "s3_keys.id"],
            name=op.f("fk_s3_key_grants_organization_id_s3_keys"),
        ),
        _tenant_fk("s3_key_grants", "volume_id", "volumes"),
        _org_fk("s3_key_grants"),
        sa.PrimaryKeyConstraint("key_id", "volume_id", name=op.f("pk_s3_key_grants")),
    )
    op.create_index(
        "ix_s3_key_grants_organization_id_owed",
        "s3_key_grants",
        ["organization_id"],
        postgresql_where=sa.text("applied_at IS NULL"),
    )


def upgrade() -> None:
    """Moves the instance and the bucket to the disk, and adds access per person and disk."""
    _refuse_organizations("upgrade")

    # organizations: no instance, no bucket, no key scope.
    op.drop_index("uq_organizations_valkey_url", table_name="organizations")
    op.drop_index("uq_organizations_s3_endpoint_s3_bucket", table_name="organizations")
    for name in (
        "valkey_url_no_credentials",
        "valkey_databases_range",
        "storage_key_scope",
        "storage_key_scope_manual",
    ):
        op.drop_constraint(op.f(f"ck_organizations_{name}"), "organizations", type_="check")
    for column in (
        "valkey_url",
        "valkey_admin_url",
        "valkey_admin_username",
        "valkey_admin_password_enc",
        "valkey_databases",
        "s3_bucket",
        "storage_key_scope",
    ):
        op.drop_column("organizations", column)

    # organization_status: metadata and storage move to each disk's volume_status.
    for name in ("metadata_state", "storage_state", "bucket_versioning"):
        op.drop_constraint(
            op.f(f"ck_organization_status_{name}"), "organization_status", type_="check"
        )
    for column in (
        "metadata_state",
        "metadata_latency_ms",
        "metadata_checked_at",
        "storage_state",
        "storage_probed_at",
        "bucket_versioning",
        "bucket_old_versions_days",
    ):
        op.drop_column("organization_status", column)

    # devices: the cleanup a revocation owes is per instance now (meta_users).
    op.drop_index("ix_devices_organization_id_cleanup_owed", table_name="devices")
    op.drop_column("devices", "valkey_user_deleted_at")

    # key_rotation_volumes: a rotation has one disk.
    op.drop_table("key_rotation_volumes")

    # volumes: database 1 of its own instance, its own bucket, its key found in s3_keys.
    op.drop_constraint("uq_volumes_organization_id_meta_db", "volumes", type_="unique")
    op.drop_constraint(op.f("ck_volumes_meta_db_positive"), "volumes", type_="check")
    op.drop_constraint(op.f("fk_volumes_organization_id_s3_keys"), "volumes", type_="foreignkey")
    op.drop_column("volumes", "meta_db")
    op.drop_column("volumes", "s3_key_id")
    op.create_index(
        "uq_volumes_s3_endpoint_s3_bucket",
        "volumes",
        ["s3_endpoint", "s3_bucket"],
        unique=True,
        postgresql_where=sa.text("state <> 'failed'"),
    )

    # volume_status: the instance's latency and the bucket's probe, per disk.
    op.add_column("volume_status", sa.Column("meta_latency_ms", sa.Integer(), nullable=True))
    op.add_column(
        "volume_status",
        sa.Column("storage_state", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
    )
    op.add_column(
        "volume_status", sa.Column("storage_probed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "volume_status",
        sa.Column(
            "bucket_versioning", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
    )
    op.add_column(
        "volume_status", sa.Column("bucket_old_versions_days", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "storage_state", "volume_status", _in("storage_state", ("ok", "failing", "unknown"))
    )
    op.create_check_constraint(
        "bucket_versioning",
        "volume_status",
        _in("bucket_versioning", ("enabled", "suspended", "off", "unknown")),
    )

    # s3_keys: a disk's key (manual), a device's key or the control plane's (managed).
    op.drop_index("uq_s3_keys_organization_id_active", table_name="s3_keys")
    op.drop_index("uq_s3_keys_membership_id_live", table_name="s3_keys")
    op.drop_constraint(op.f("ck_s3_keys_holder"), "s3_keys", type_="check")
    op.drop_constraint(op.f("ck_s3_keys_person_holder"), "s3_keys", type_="check")
    op.drop_constraint(
        op.f("fk_s3_keys_organization_id_memberships"), "s3_keys", type_="foreignkey"
    )
    op.drop_column("s3_keys", "membership_id")
    op.alter_column("s3_keys", "holder", existing_type=sa.Text(), server_default=None)
    op.add_column("s3_keys", sa.Column("volume_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_s3_keys_organization_id_volumes"),
        "s3_keys",
        "volumes",
        ["organization_id", "volume_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "holder", "s3_keys", _in("holder", ("disk", "device", "control_plane"))
    )
    op.create_check_constraint(
        "disk_holder", "s3_keys", "(holder = 'disk') = (volume_id IS NOT NULL)"
    )
    op.create_check_constraint(
        "holder_origin", "s3_keys", "(holder = 'disk') = (origin = 'pasted')"
    )
    op.create_index(
        "uq_s3_keys_volume_id_active",
        "s3_keys",
        ["volume_id"],
        unique=True,
        postgresql_where=sa.text("holder = 'disk' AND state = 'active'"),
    )
    op.create_index(
        "uq_s3_keys_organization_id_control_plane",
        "s3_keys",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("holder = 'control_plane' AND state = 'active'"),
    )

    # key_rotations: one disk, its config step, devices instead of Macs, no adapter trigger.
    op.drop_index("uq_key_rotations_organization_id_applying", table_name="key_rotations")
    op.drop_index("uq_key_rotations_organization_id_awaiting", table_name="key_rotations")
    for name in ("trigger_started_by", "trigger", "macs_total_positive"):
        op.drop_constraint(op.f(f"ck_key_rotations_{name}"), "key_rotations", type_="check")
    op.drop_column("key_rotations", "trigger")
    op.alter_column("key_rotations", "started_by_user_id", existing_type=sa.UUID(), nullable=False)
    op.alter_column("key_rotations", "macs_total", new_column_name="devices_total")
    op.create_check_constraint("devices_total_positive", "key_rotations", "devices_total >= 0")
    op.add_column("key_rotations", sa.Column("volume_id", sa.UUID(), nullable=False))
    op.add_column(
        "key_rotations",
        sa.Column("config_state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    )
    op.add_column("key_rotations", sa.Column("config_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "config_state",
        "key_rotations",
        _in("config_state", ("pending", "running", "done", "failed")),
    )
    op.create_foreign_key(
        op.f("fk_key_rotations_organization_id_volumes"),
        "key_rotations",
        "volumes",
        ["organization_id", "volume_id"],
        ["organization_id", "id"],
    )
    op.create_index(
        "uq_key_rotations_volume_id_applying",
        "key_rotations",
        ["volume_id"],
        unique=True,
        postgresql_where=sa.text("state = 'applying'"),
    )
    op.create_index(
        "uq_key_rotations_volume_id_awaiting",
        "key_rotations",
        ["volume_id"],
        unique=True,
        postgresql_where=sa.text("state = 'awaiting_old_key_deletion'"),
    )

    # key_exposures: per disk, and a person lowered to none on a disk.
    op.drop_index("ix_key_exposures_organization_id_open", table_name="key_exposures")
    op.drop_constraint(op.f("ck_key_exposures_kind"), "key_exposures", type_="check")
    op.drop_constraint(op.f("ck_key_exposures_person_removed"), "key_exposures", type_="check")
    op.add_column("key_exposures", sa.Column("volume_id", sa.UUID(), nullable=False))
    op.create_foreign_key(
        op.f("fk_key_exposures_organization_id_volumes"),
        "key_exposures",
        "volumes",
        ["organization_id", "volume_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "kind", "key_exposures", _in("kind", ("person_removed", "device_revoked", "access_removed"))
    )
    op.create_check_constraint(
        "membership_kind",
        "key_exposures",
        "(kind IN ('person_removed', 'access_removed')) = (membership_id IS NOT NULL)",
    )
    op.create_index(
        "ix_key_exposures_organization_id_volume_id_open",
        "key_exposures",
        ["organization_id", "volume_id"],
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    # audit_events: access changes.
    op.drop_constraint(op.f("ck_audit_events_action"), "audit_events", type_="check")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0003))

    _create_tables()
    _grant_new_tables()


def downgrade() -> None:
    """Back to 0002 on an empty database: one instance and one bucket per organization."""
    _refuse_organizations("downgrade")
    for table in reversed(NEW_TABLES):
        op.drop_table(table)

    op.drop_constraint(op.f("ck_audit_events_action"), "audit_events", type_="check")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0002))

    op.drop_index("ix_key_exposures_organization_id_volume_id_open", table_name="key_exposures")
    op.drop_constraint(op.f("ck_key_exposures_membership_kind"), "key_exposures", type_="check")
    op.drop_constraint(op.f("ck_key_exposures_kind"), "key_exposures", type_="check")
    op.drop_constraint(
        op.f("fk_key_exposures_organization_id_volumes"), "key_exposures", type_="foreignkey"
    )
    op.drop_column("key_exposures", "volume_id")
    op.create_check_constraint(
        "kind", "key_exposures", _in("kind", ("person_removed", "device_revoked"))
    )
    op.create_check_constraint(
        "person_removed", "key_exposures", "(kind = 'person_removed') = (membership_id IS NOT NULL)"
    )
    op.create_index(
        "ix_key_exposures_organization_id_open",
        "key_exposures",
        ["organization_id"],
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    op.drop_index("uq_key_rotations_volume_id_awaiting", table_name="key_rotations")
    op.drop_index("uq_key_rotations_volume_id_applying", table_name="key_rotations")
    op.drop_constraint(
        op.f("fk_key_rotations_organization_id_volumes"), "key_rotations", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_key_rotations_config_state"), "key_rotations", type_="check")
    op.drop_constraint(
        op.f("ck_key_rotations_devices_total_positive"), "key_rotations", type_="check"
    )
    for column in ("config_error", "config_state", "volume_id"):
        op.drop_column("key_rotations", column)
    op.alter_column("key_rotations", "devices_total", new_column_name="macs_total")
    op.create_check_constraint("macs_total_positive", "key_rotations", "macs_total >= 0")
    op.alter_column("key_rotations", "started_by_user_id", existing_type=sa.UUID(), nullable=True)
    op.add_column(
        "key_rotations",
        sa.Column("trigger", sa.Text(), server_default=sa.text("'admin'"), nullable=False),
    )
    op.create_check_constraint("trigger", "key_rotations", _in("trigger", ("admin", "revocation")))
    op.create_check_constraint(
        "trigger_started_by",
        "key_rotations",
        "(trigger = 'admin') = (started_by_user_id IS NOT NULL)",
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

    op.drop_index("uq_s3_keys_organization_id_control_plane", table_name="s3_keys")
    op.drop_index("uq_s3_keys_volume_id_active", table_name="s3_keys")
    for name in ("holder_origin", "disk_holder", "holder"):
        op.drop_constraint(op.f(f"ck_s3_keys_{name}"), "s3_keys", type_="check")
    op.drop_constraint(op.f("fk_s3_keys_organization_id_volumes"), "s3_keys", type_="foreignkey")
    op.drop_column("s3_keys", "volume_id")
    op.alter_column("s3_keys", "holder", existing_type=sa.Text(), server_default=sa.text("'team'"))
    op.add_column("s3_keys", sa.Column("membership_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_s3_keys_organization_id_memberships"),
        "s3_keys",
        "memberships",
        ["organization_id", "membership_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "holder", "s3_keys", _in("holder", ("team", "person", "device", "control_plane"))
    )
    op.create_check_constraint(
        "person_holder", "s3_keys", "(holder = 'person') = (membership_id IS NOT NULL)"
    )
    op.create_index(
        "uq_s3_keys_organization_id_active",
        "s3_keys",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active' AND holder IN ('team', 'control_plane')"),
    )
    op.create_index(
        "uq_s3_keys_membership_id_live",
        "s3_keys",
        ["membership_id"],
        unique=True,
        postgresql_where=sa.text("holder = 'person' AND state IN ('pending', 'active')"),
    )

    op.drop_constraint(op.f("ck_volume_status_bucket_versioning"), "volume_status", type_="check")
    op.drop_constraint(op.f("ck_volume_status_storage_state"), "volume_status", type_="check")
    for column in (
        "bucket_old_versions_days",
        "bucket_versioning",
        "storage_probed_at",
        "storage_state",
        "meta_latency_ms",
    ):
        op.drop_column("volume_status", column)

    op.drop_index("uq_volumes_s3_endpoint_s3_bucket", table_name="volumes")
    op.add_column("volumes", sa.Column("s3_key_id", sa.UUID(), nullable=True))
    op.add_column("volumes", sa.Column("meta_db", sa.Integer(), nullable=False))
    op.create_foreign_key(
        op.f("fk_volumes_organization_id_s3_keys"),
        "volumes",
        "s3_keys",
        ["organization_id", "s3_key_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint("meta_db_positive", "volumes", "meta_db >= 1")
    op.create_unique_constraint(
        "uq_volumes_organization_id_meta_db", "volumes", ["organization_id", "meta_db"]
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
            "state IN ('pending', 'running', 'done', 'failed')",
            name=op.f("ck_key_rotation_volumes_state"),
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
        _org_fk("key_rotation_volumes"),
        sa.PrimaryKeyConstraint("rotation_id", "volume_id", name=op.f("pk_key_rotation_volumes")),
    )
    exists = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE})
        .scalar_one_or_none()
    )
    if exists is not None:
        role = op.get_bind().dialect.identifier_preparer.quote(APP_ROLE)
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON key_rotation_volumes TO {role}")

    op.add_column(
        "devices", sa.Column("valkey_user_deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_devices_organization_id_cleanup_owed",
        "devices",
        ["organization_id"],
        postgresql_where=sa.text("state = 'revoked' AND valkey_user_deleted_at IS NULL"),
    )

    op.add_column(
        "organization_status",
        sa.Column("metadata_state", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
    )
    op.add_column(
        "organization_status", sa.Column("metadata_latency_ms", sa.Integer(), nullable=True)
    )
    op.add_column(
        "organization_status",
        sa.Column("metadata_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organization_status",
        sa.Column("storage_state", sa.Text(), server_default=sa.text("'ok'"), nullable=False),
    )
    op.add_column(
        "organization_status",
        sa.Column("storage_probed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organization_status",
        sa.Column(
            "bucket_versioning", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
    )
    op.add_column(
        "organization_status", sa.Column("bucket_old_versions_days", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "metadata_state", "organization_status", _in("metadata_state", ("ok", "down"))
    )
    op.create_check_constraint(
        "storage_state", "organization_status", _in("storage_state", ("ok", "failing"))
    )
    op.create_check_constraint(
        "bucket_versioning",
        "organization_status",
        _in("bucket_versioning", ("enabled", "suspended", "off", "unknown")),
    )

    op.add_column("organizations", sa.Column("valkey_url", sa.Text(), nullable=False))
    op.add_column("organizations", sa.Column("valkey_admin_url", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("valkey_admin_username", sa.Text(), nullable=True))
    op.add_column(
        "organizations", sa.Column("valkey_admin_password_enc", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column("valkey_databases", sa.Integer(), server_default=sa.text("16"), nullable=False),
    )
    op.add_column("organizations", sa.Column("s3_bucket", sa.Text(), nullable=False))
    op.add_column(
        "organizations",
        sa.Column("storage_key_scope", sa.Text(), server_default=sa.text("'team'"), nullable=False),
    )
    op.create_check_constraint(
        "valkey_url_no_credentials", "organizations", "position('@' in valkey_url) = 0"
    )
    op.create_check_constraint(
        "valkey_databases_range", "organizations", "valkey_databases BETWEEN 2 AND 1024"
    )
    op.create_check_constraint(
        "storage_key_scope", "organizations", _in("storage_key_scope", ("team", "person", "device"))
    )
    op.create_check_constraint(
        "storage_key_scope_manual",
        "organizations",
        "storage_mode = 'managed' OR storage_key_scope IN ('team', 'person')",
    )
    op.create_index(
        "uq_organizations_valkey_url",
        "organizations",
        [sa.text("lower(valkey_url)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_organizations_s3_endpoint_s3_bucket",
        "organizations",
        ["s3_endpoint", "s3_bucket"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
