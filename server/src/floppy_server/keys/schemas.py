"""The S3 keys' API shapes: each disk's key, its risk window and a rotation's progress."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from floppy_server.api import CamelModel

StepState = Literal["pending", "running", "done", "failed"]


class KeyWindowReason(CamelModel):
    """Why a disk's window is open: someone who could read its key lost access to it."""

    id: UUID
    kind: Literal["person_removed", "device_revoked", "access_removed"]
    label: str
    at: datetime


class KeyWindow(CamelModel):
    """Open from the first exposure until a rotation of the disk's key closes it."""

    opened_at: datetime
    reasons: list[KeyWindowReason]


class RotationDevices(CamelModel):
    """Devices that picked up the new key, out of those connected and seeing the disk at start."""

    updated: int
    total: int


class RotationError(CamelModel):
    """The provider's answer when the probe failed, or JuiceFS's when the config did."""

    step: Literal["probe", "config"]
    code: str
    request: str


class RotationOut(CamelModel):
    """A rotation of one disk's key."""

    id: UUID
    disk_id: UUID
    disk_name: str
    state: Literal["applying", "awaiting_old_key_deletion", "failed"]
    new_key_tail: str
    # Every key of the disk to delete at the provider: the one replaced and those of
    # superseded rotations.
    old_key_tails: list[str]
    probe: StepState
    config: StepState
    devices: RotationDevices
    error: RotationError | None
    started_at: datetime


class Versioning(CamelModel):
    """A bucket's versioning as the last probe read it."""

    state: Literal["enabled", "suspended", "off", "unknown"]
    old_versions_days: int | None


class DiskKeyOut(CamelModel):
    """
    A disk's storage: its bucket, and in manual mode the key its format holds, with the window
    and the rotation. In managed mode `accessKeyTail` is null and there is no window.
    """

    disk_id: UUID
    disk_name: str
    bucket: str
    endpoint: str
    access_key_tail: str | None
    rotated_at: datetime | None
    window: KeyWindow | None
    rotation: RotationOut | None
    versioning: Versioning


class DeviceKeyOut(CamelModel):
    """A device's own key (managed): live, or `retiring` while its deletion is owed."""

    id: UUID
    device_id: UUID
    owner: str
    access_key_tail: str
    state: Literal["pending", "active", "retiring"]
    activated_at: datetime | None
    retiring_at: datetime | None


class KeyStatusOut(CamelModel):
    """The organization's storage keys: each disk's (manual) or each device's (managed)."""

    storage_mode: Literal["manual", "managed"]
    storage_provider: Literal["ovh", "aws", "minio", "garage"] | None
    control_plane_key_tail: str | None
    disks: list[DiskKeyOut]
    keys: list[DeviceKeyOut]


class StartRotationRequest(CamelModel):
    """A disk and its new pasted key; the secret is sent once and never returned."""

    disk_id: UUID
    access_key_id: str = Field(min_length=8, max_length=256, pattern=r"^\S+$")
    secret_key: str = Field(min_length=8, max_length=512, pattern=r"^\S+$")

    def __repr__(self) -> str:
        return f"StartRotationRequest(access_key_id=...{self.access_key_id[-5:]})"


class CloseRotationRequest(CamelModel):
    """The admin confirms the old key is deleted at the provider."""

    old_key_deleted: Literal[True]


class S3KeyIn(CamelModel):
    """A pasted S3 key; the secret is never returned."""

    access_key_id: str = Field(min_length=8, max_length=256, pattern=r"^\S+$")
    secret_key: str = Field(min_length=8, max_length=512, pattern=r"^\S+$")

    def __repr__(self) -> str:
        return f"S3KeyIn(access_key_id=...{self.access_key_id[-5:]})"
