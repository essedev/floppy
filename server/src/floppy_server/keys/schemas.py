"""The S3 key's API shapes: its status, the risk window and a rotation's progress."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from floppy_server.api import CamelModel

StepState = Literal["pending", "running", "done", "failed"]


class KeyWindowReason(CamelModel):
    """Why the window is open: someone who had the key no longer has access."""

    id: UUID
    kind: Literal["person_removed", "device_revoked"]
    label: str
    at: datetime


class KeyWindow(CamelModel):
    """Open from the first removal or revocation until a rotation closes it."""

    opened_at: datetime
    reasons: list[KeyWindowReason]


class RotationDisk(CamelModel):
    """One disk's step in a rotation."""

    id: UUID
    name: str
    state: StepState


class RotationMacs(CamelModel):
    """Macs that picked up the new key, out of those connected when the rotation started."""

    updated: int
    total: int


class RotationError(CamelModel):
    """The provider's answer when the probe failed, or JuiceFS's when a disk did."""

    step: Literal["probe", "disk"]
    code: str
    request: str


class RotationOut(CamelModel):
    """A key rotation."""

    id: UUID
    state: Literal["applying", "awaiting_old_key_deletion", "failed"]
    new_key_tail: str
    # Every key to delete at the provider: the one replaced and those of superseded rotations.
    old_key_tails: list[str]
    probe: StepState
    disks: list[RotationDisk]
    macs: RotationMacs
    error: RotationError | None
    started_at: datetime


class Versioning(CamelModel):
    """The bucket's versioning as the last probe read it with the control plane's key."""

    state: Literal["enabled", "suspended", "off", "unknown"]
    old_versions_days: int | None


class ScopedKey(CamelModel):
    """A person's or a Mac's key: live, or `retiring` while its deletion at the provider is owed."""

    id: UUID
    holder: Literal["person", "device"]
    owner: str
    access_key_tail: str
    state: Literal["pending", "active", "retiring"]
    origin: Literal["pasted", "provider"]
    activated_at: datetime | None
    retiring_at: datetime | None


class KeyStatusOut(CamelModel):
    """
    The organization's S3 keys. With a team key: the key in the formats, its window and its
    rotation. With person or Mac keys (`keyScope`): the control plane's own key in
    `accessKeyTail`, and every person's or Mac's key in `keys`, no window.
    """

    bucket: str
    storage_mode: Literal["manual", "managed"]
    storage_provider: Literal["ovh", "aws", "minio", "garage"] | None
    key_scope: Literal["team", "person", "device"]
    access_key_tail: str
    rotated_at: datetime | None
    window: KeyWindow | None
    rotation: RotationOut | None
    keys: list[ScopedKey]
    versioning: Versioning


class StartRotationRequest(CamelModel):
    """A new pasted key (manual); the secret is sent once and never returned."""

    access_key_id: str = Field(min_length=8, max_length=256, pattern=r"^\S+$")
    secret_key: str = Field(min_length=8, max_length=512, pattern=r"^\S+$")

    def __repr__(self) -> str:
        return f"StartRotationRequest(access_key_id=...{self.access_key_id[-5:]})"


class CloseRotationRequest(CamelModel):
    """The admin confirms the old key is deleted at the provider."""

    old_key_deleted: Literal[True]
