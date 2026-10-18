"""
Deleting a disk: the audit action `disk_deleted` (docs/DATABASE_SCHEMA.md, "volumes").

The disk is soft deleted; its instance, metadata and objects stay. Only the audit log's list
of actions changes.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS_0003 = (
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
)
ACTIONS_0004 = (*ACTIONS_0003, "disk_deleted")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    """Adds `disk_deleted` to the audit actions."""
    op.drop_constraint(op.f("ck_audit_events_action"), "audit_events", type_="check")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0004))


def downgrade() -> None:
    """Refuses while a `disk_deleted` event exists: the audit log is never rewritten."""
    count = (
        op.get_bind()
        .exec_driver_sql("SELECT count(*) FROM audit_events WHERE action = 'disk_deleted'")
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            f"migration 0004 downgrade: the audit log holds {count} disk_deleted event(s)"
        )
    op.drop_constraint(op.f("ck_audit_events_action"), "audit_events", type_="check")
    op.create_check_constraint("action", "audit_events", _in("action", ACTIONS_0003))
