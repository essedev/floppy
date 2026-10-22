"""
What a tool does to the world, as each tool declares it: the MCP annotations, and the source
of the scope a call needs (`oauth/scopes.py`). Hints, not permissions: the services and the
role decide.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolHints:
    """The four MCP annotations of a tool."""

    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


READS = ToolHints(read_only=True, idempotent=True)
# Reads that touch an external system (a test write on a bucket, a test mail).
PROBES = ToolHints(idempotent=True, open_world=True)
CREATES = ToolHints()
UPDATES = ToolHints(idempotent=True)
DESTROYS = ToolHints(destructive=True, idempotent=True)
