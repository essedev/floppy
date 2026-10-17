"""Floppy's control plane: organizations, people, devices, disks and their access, off the data path."""

import sys


def main() -> None:
    """The `floppy-server` command."""
    from floppy_server.cli import main as cli_main  # noqa: PLC0415 (no import cost for library users)

    sys.exit(cli_main())
