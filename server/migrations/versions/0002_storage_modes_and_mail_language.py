"""
Storage modes and key scopes, bucket versioning, the mail language, and the managed entity
called a device instead of a Mac in the audit log and the key exposures (docs/DATABASE_SCHEMA.md,
"Migration 0002").

Every change keeps existing rows valid: an organization from 0001 is a manual one with a
team key, in Italian. Autogenerate found the columns, indexes and foreign keys; the CHECK
constraints and the changed partial index are written by hand.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS_0001 = (
    "signed_in", "sign_in_locked", "person_invited", "invite_resent", "invite_accepted",
    "role_changed", "person_removed", "mac_registered", "mac_revoked", "mac_signed_out",
    "mac_seen_outdated", "key_rotation_started", "key_rotated", "key_rotation_failed",
    "key_window_closed", "disk_created", "disk_updated", "settings_updated",
)
# The managed entity is called a device, not a Mac: actions, categories, targets and the
# exposure kind say so from 0002 on, existing rows included.
DEVICE_RENAMES = {
    "mac_registered": "device_registered",
    "mac_revoked": "device_revoked",
    "mac_signed_out": "device_signed_out",
    "mac_seen_outdated": "device_seen_outdated",
}
ACTIONS_0002 = (
    "organization_created",
    *(DEVICE_RENAMES.get(action, action) for action in ACTIONS_0001),
    "key_created",
    "key_deleted",
)
CATEGORIES_0001 = ("access", "people", "macs", "key", "disks", "settings")
CATEGORIES_0002 = ("access", "people", "devices", "key", "disks", "settings")
TARGETS_0001 = ("person", "mac", "disk", "key", "organization")
TARGETS_0002 = ("person", "device", "disk", "key", "organization")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    """Adds the storage, versioning and language columns with their constraints."""
    # organizations
    op.add_column("organizations", sa.Column("mail_language", sa.Text(), server_default=sa.text("'it'"), nullable=False))
    op.add_column("organizations", sa.Column("storage_mode", sa.Text(), server_default=sa.text("'manual'"), nullable=False))
    op.add_column("organizations", sa.Column("storage_provider", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("storage_key_scope", sa.Text(), server_default=sa.text("'team'"), nullable=False))
    op.add_column("organizations", sa.Column("storage_admin_credentials_enc", sa.LargeBinary(), nullable=True))
    op.create_check_constraint("mail_language", "organizations", _in("mail_language", ("it", "en")))
    op.create_check_constraint("storage_mode", "organizations", _in("storage_mode", ("manual", "managed")))
    op.create_check_constraint("storage_provider", "organizations", _in("storage_provider", ("ovh", "aws", "minio", "garage")))
    op.create_check_constraint("storage_provider_managed", "organizations", "(storage_mode = 'managed') = (storage_provider IS NOT NULL)")
    op.create_check_constraint("storage_key_scope", "organizations", _in("storage_key_scope", ("team", "person", "device")))
    op.create_check_constraint("storage_key_scope_manual", "organizations", "storage_mode = 'managed' OR storage_key_scope IN ('team', 'person')")
    op.create_check_constraint("storage_admin_credentials_managed", "organizations", "storage_mode = 'managed' OR storage_admin_credentials_enc IS NULL")

    # organization_status
    op.add_column("organization_status", sa.Column("bucket_versioning", sa.Text(), server_default=sa.text("'unknown'"), nullable=False))
    op.add_column("organization_status", sa.Column("bucket_old_versions_days", sa.Integer(), nullable=True))
    op.create_check_constraint("bucket_versioning", "organization_status", _in("bucket_versioning", ("enabled", "suspended", "off", "unknown")))

    # s3_keys
    op.add_column("s3_keys", sa.Column("holder", sa.Text(), server_default=sa.text("'team'"), nullable=False))
    op.add_column("s3_keys", sa.Column("membership_id", sa.UUID(), nullable=True))
    op.add_column("s3_keys", sa.Column("device_id", sa.UUID(), nullable=True))
    op.add_column("s3_keys", sa.Column("origin", sa.Text(), server_default=sa.text("'pasted'"), nullable=False))
    op.add_column("s3_keys", sa.Column("provider_ref", sa.Text(), nullable=True))
    op.create_foreign_key(op.f("fk_s3_keys_organization_id_memberships"), "s3_keys", "memberships", ["organization_id", "membership_id"], ["organization_id", "id"])
    op.create_foreign_key(op.f("fk_s3_keys_organization_id_devices"), "s3_keys", "devices", ["organization_id", "device_id"], ["organization_id", "id"])
    op.drop_constraint(op.f("ck_s3_keys_secret_wiped"), "s3_keys", type_="check")
    op.create_check_constraint("secret_wiped", "s3_keys", "(holder = 'device' OR state IN ('retired', 'rejected')) = (secret_key_enc IS NULL)")
    op.create_check_constraint("holder", "s3_keys", _in("holder", ("team", "person", "device", "control_plane")))
    op.create_check_constraint("person_holder", "s3_keys", "(holder = 'person') = (membership_id IS NOT NULL)")
    op.create_check_constraint("device_holder", "s3_keys", "(holder = 'device') = (device_id IS NOT NULL)")
    op.create_check_constraint("origin", "s3_keys", _in("origin", ("pasted", "provider")))
    op.create_check_constraint("provider_ref", "s3_keys", "(origin = 'provider') = (provider_ref IS NOT NULL)")
    op.drop_index("uq_s3_keys_organization_id_active", table_name="s3_keys")
    op.create_index("uq_s3_keys_organization_id_active", "s3_keys", ["organization_id"], unique=True, postgresql_where=sa.text("state = 'active' AND holder IN ('team', 'control_plane')"))
    op.create_index("uq_s3_keys_device_id_live", "s3_keys", ["device_id"], unique=True, postgresql_where=sa.text("holder = 'device' AND state IN ('pending', 'active')"))
    op.create_index("uq_s3_keys_membership_id_live", "s3_keys", ["membership_id"], unique=True, postgresql_where=sa.text("holder = 'person' AND state IN ('pending', 'active')"))
    op.create_index("ix_s3_keys_organization_id_deletion_owed", "s3_keys", ["organization_id"], unique=False, postgresql_where=sa.text("origin = 'provider' AND state = 'retiring'"))

    # key_rotations
    op.alter_column("key_rotations", "started_by_user_id", existing_type=sa.UUID(), nullable=True)
    op.add_column("key_rotations", sa.Column("trigger", sa.Text(), server_default=sa.text("'admin'"), nullable=False))
    op.create_check_constraint("trigger", "key_rotations", _in("trigger", ("admin", "revocation")))
    op.create_check_constraint("trigger_started_by", "key_rotations", "(trigger = 'admin') = (started_by_user_id IS NOT NULL)")

    # volumes: ready with no key in the format, for person and device scopes
    op.drop_constraint(op.f("ck_volumes_ready"), "volumes", type_="check")
    op.create_check_constraint("ready", "volumes", "state <> 'ready' OR juicefs_uuid IS NOT NULL")

    # audit_events: new actions, and Mac renamed to device. The owner runs the migration,
    # the one role the append-only trigger lets rewrite rows.
    for name in ("action", "category", "target_kind"):
        op.drop_constraint(op.f(f"ck_audit_events_{name}"), "audit_events", type_="check")
    for old, new in DEVICE_RENAMES.items():
        op.execute(sa.text("UPDATE audit_events SET action = :new WHERE action = :old").bindparams(old=old, new=new))
    op.execute("UPDATE audit_events SET category = 'devices' WHERE category = 'macs'")
    op.execute("UPDATE audit_events SET target_kind = 'device' WHERE target_kind = 'mac'")
    op.execute(
        "UPDATE audit_events SET detail = (detail - 'macs') || jsonb_build_object('devices', detail -> 'macs') "
        "WHERE action = 'person_removed' AND detail ? 'macs'"
    )
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0002))
    op.create_check_constraint("category", "audit_events", _in("category", CATEGORIES_0002))
    op.create_check_constraint("target_kind", "audit_events", _in("target_kind", TARGETS_0002))

    # key_exposures: the kind a revoked device opens
    op.drop_constraint(op.f("ck_key_exposures_kind"), "key_exposures", type_="check")
    op.drop_constraint(op.f("ck_key_exposures_mac_revoked"), "key_exposures", type_="check")
    op.execute("UPDATE key_exposures SET kind = 'device_revoked' WHERE kind = 'mac_revoked'")
    op.create_check_constraint("kind", "key_exposures", _in("kind", ("person_removed", "device_revoked")))
    op.create_check_constraint("device_revoked", "key_exposures", "(kind = 'device_revoked') = (device_id IS NOT NULL)")


def downgrade() -> None:
    """Back to 0001. For development only: rows using the new values make it fail."""
    op.drop_constraint(op.f("ck_key_exposures_device_revoked"), "key_exposures", type_="check")
    op.drop_constraint(op.f("ck_key_exposures_kind"), "key_exposures", type_="check")
    op.execute("UPDATE key_exposures SET kind = 'mac_revoked' WHERE kind = 'device_revoked'")
    op.create_check_constraint("kind", "key_exposures", _in("kind", ("person_removed", "mac_revoked")))
    op.create_check_constraint("mac_revoked", "key_exposures", "(kind = 'mac_revoked') = (device_id IS NOT NULL)")

    for name in ("action", "category", "target_kind"):
        op.drop_constraint(op.f(f"ck_audit_events_{name}"), "audit_events", type_="check")
    for old, new in DEVICE_RENAMES.items():
        op.execute(sa.text("UPDATE audit_events SET action = :old WHERE action = :new").bindparams(old=old, new=new))
    op.execute("UPDATE audit_events SET category = 'macs' WHERE category = 'devices'")
    op.execute("UPDATE audit_events SET target_kind = 'mac' WHERE target_kind = 'device'")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0001))
    op.create_check_constraint("category", "audit_events", _in("category", CATEGORIES_0001))
    op.create_check_constraint("target_kind", "audit_events", _in("target_kind", TARGETS_0001))

    op.drop_constraint(op.f("ck_volumes_ready"), "volumes", type_="check")
    op.create_check_constraint("ready", "volumes", "state <> 'ready' OR (s3_key_id IS NOT NULL AND juicefs_uuid IS NOT NULL)")

    op.drop_constraint(op.f("ck_key_rotations_trigger_started_by"), "key_rotations", type_="check")
    op.drop_constraint(op.f("ck_key_rotations_trigger"), "key_rotations", type_="check")
    op.drop_column("key_rotations", "trigger")
    op.alter_column("key_rotations", "started_by_user_id", existing_type=sa.UUID(), nullable=False)

    op.drop_index("ix_s3_keys_organization_id_deletion_owed", table_name="s3_keys")
    op.drop_index("uq_s3_keys_membership_id_live", table_name="s3_keys")
    op.drop_index("uq_s3_keys_device_id_live", table_name="s3_keys")
    op.drop_index("uq_s3_keys_organization_id_active", table_name="s3_keys")
    op.create_index("uq_s3_keys_organization_id_active", "s3_keys", ["organization_id"], unique=True, postgresql_where=sa.text("state = 'active'"))
    for name in ("provider_ref", "origin", "device_holder", "person_holder", "holder", "secret_wiped"):
        op.drop_constraint(op.f(f"ck_s3_keys_{name}"), "s3_keys", type_="check")
    op.create_check_constraint("secret_wiped", "s3_keys", "(state IN ('retired', 'rejected')) = (secret_key_enc IS NULL)")
    op.drop_constraint(op.f("fk_s3_keys_organization_id_devices"), "s3_keys", type_="foreignkey")
    op.drop_constraint(op.f("fk_s3_keys_organization_id_memberships"), "s3_keys", type_="foreignkey")
    for column in ("provider_ref", "origin", "device_id", "membership_id", "holder"):
        op.drop_column("s3_keys", column)

    op.drop_constraint(op.f("ck_organization_status_bucket_versioning"), "organization_status", type_="check")
    op.drop_column("organization_status", "bucket_old_versions_days")
    op.drop_column("organization_status", "bucket_versioning")

    for name in (
        "storage_admin_credentials_managed", "storage_key_scope_manual", "storage_key_scope",
        "storage_provider_managed", "storage_provider", "storage_mode", "mail_language",
    ):
        op.drop_constraint(op.f(f"ck_organizations_{name}"), "organizations", type_="check")
    for column in ("storage_admin_credentials_enc", "storage_key_scope", "storage_provider", "storage_mode", "mail_language"):
        op.drop_column("organizations", column)
