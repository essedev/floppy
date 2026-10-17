"""
The instances on the service's volume: one directory per instance with its record, its
Valkey configuration, its ACL file and its data.

`/data/instances/<name>/instance.json` is the record (label, port, admin password, limits,
whether it should run); `valkey.conf` is rendered from it at every start, so a change of the
template or of a limit applies at the next start. A discarded instance moves to
`/data/trash/`, never deleted by the service: what is left there is the operator's to remove.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from floppy_meta.config import Settings

# The control plane names an instance by its disk's id (a UUID); anything else that fits a
# directory name and a log line is accepted, nothing that could climb out of the directory.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Valkey's databases: 0 stays empty, the disk's metadata is in 1 (the control plane's META_DB).
DATABASES = 2

Desired = Literal["running", "stopped"]


class StoreError(Exception):
    """A request the store cannot satisfy; the message names no secret."""


@dataclass
class Instance:
    """One disk's instance as the service keeps it."""

    name: str
    label: str
    port: int
    admin_password: str
    desired: Desired
    created_at: str
    maxmemory_mb: int | None = None

    def __repr__(self) -> str:
        return f"Instance(name={self.name!r}, label={self.label!r}, port={self.port})"


def new_label() -> str:
    """
    An opaque DNS label for an instance's host name: 80 random bits in lowercase base32.
    Never the disk's name: host names reach DNS resolvers and logs (and certificate
    transparency, were the certificate not a wildcard).
    """
    return base64.b32encode(secrets.token_bytes(10)).decode().lower()


def new_password() -> str:
    """An admin password: 32 random bytes, URL-safe."""
    return secrets.token_urlsafe(32)


class Store:
    """The records on disk; callers hold the service's lock around changes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.root = settings.data_dir / "instances"
        self.trash = settings.data_dir / "trash"
        self.root.mkdir(parents=True, exist_ok=True)
        self.trash.mkdir(parents=True, exist_ok=True)

    def directory(self, name: str) -> Path:
        """An instance's directory; refuses a name that is not one."""
        if not NAME.match(name):
            raise StoreError("not an instance name")
        return self.root / name

    def load_all(self) -> list[Instance]:
        """Every instance on the volume, in name order."""
        found = []
        for entry in sorted(self.root.iterdir()):
            record = entry / "instance.json"
            if entry.is_dir() and record.is_file():
                found.append(Instance(**json.loads(record.read_text())))
        return found

    def get(self, name: str) -> Instance | None:
        """The instance called `name`, if the volume has it."""
        record = self.directory(name) / "instance.json"
        if not record.is_file():
            return None
        return Instance(**json.loads(record.read_text()))

    def create(self, name: str, maxmemory_mb: int | None) -> Instance:
        """A new instance's directory, record and ACL file, on the lowest free port."""
        directory = self.directory(name)
        used = {i.port for i in self.load_all()}
        port = next(
            (
                p
                for p in range(self._settings.port_min, self._settings.port_max + 1)
                if p not in used
            ),
            None,
        )
        if port is None:
            raise StoreError("no free port: every port of FLOPPY_META_PORT_MIN..MAX is taken")
        labels = {i.label for i in self.load_all()}
        label = new_label()
        while label in labels:
            label = new_label()
        instance = Instance(
            name=name,
            label=label,
            port=port,
            admin_password=new_password(),
            desired="running",
            created_at=datetime.now(UTC).isoformat(),
            maxmemory_mb=maxmemory_mb,
        )
        directory.mkdir(mode=0o700)
        self.write_acl(instance)
        self.save(instance)
        return instance

    def save(self, instance: Instance) -> None:
        """Writes the record atomically, readable by the service's user only."""
        directory = self.directory(instance.name)
        tmp = directory / "instance.json.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(instance), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(directory / "instance.json")

    def write_acl(self, instance: Instance) -> None:
        """
        The ACL file of a new instance: the admin (Valkey's `default` user) with the hash of
        its password. The devices' users are added by the control plane and saved there by
        `ACL SAVE`.
        """
        digest = hashlib.sha256(instance.admin_password.encode()).hexdigest()
        path = self.directory(instance.name) / "users.acl"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"user default on #{digest} ~* &* +@all\n")

    def render_config(self, instance: Instance) -> Path:
        """`valkey.conf` for the instance, from the current settings and its record."""
        s = self._settings
        directory = self.directory(instance.name)
        lines = [
            "# Written by the meta service at every start; edits here are lost.",
            "bind * -::*",
            f"port {instance.port}",
            # The admin and every device authenticate; the ACL file has no user without a
            # password, so protected mode would only refuse the private network.
            "protected-mode no",
            "daemonize no",
            'logfile ""',
            f"dir {directory}",
            f"aclfile {directory / 'users.acl'}",
            f"databases {DATABASES}",
            # JuiceFS's metadata must never be evicted: a full instance refuses writes instead.
            "maxmemory-policy noeviction",
            f"maxmemory {instance.maxmemory_mb or 0}mb",
            # AOF for every write plus RDB snapshots, as JuiceFS's Redis guide recommends;
            # `always` puts each write on disk before the answer.
            "appendonly yes",
            f"appendfsync {s.appendfsync}",
            "aof-use-rdb-preamble yes",
            "save 3600 1 300 100 60 10000",
            # A device's replica full-syncs at once instead of waiting for others (DECISIONS
            # #23: 1.8 s instead of 5.6 s for three disks).
            "repl-diskless-sync yes",
            "repl-diskless-sync-delay 0",
            f"repl-backlog-size {s.repl_backlog_mb}mb",
            "tcp-keepalive 60",
            "",
        ]
        path = directory / "valkey.conf"
        path.write_text("\n".join(lines))
        return path

    def discard(self, name: str) -> Path:
        """Moves an instance's directory to the trash; returns where it went."""
        directory = self.directory(name)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.trash / f"{name}.{stamp}.{secrets.token_hex(3)}"
        shutil.move(directory, target)
        return target
