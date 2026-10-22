"""
The MCP surface's three scopes, read from what each tool declares it does.

The scope says what a token may do to the team's state; the role says what the person may
do at all. A call passes both: an admin's read-only token cannot remove a person, and a
member's full token still cannot create a disk.
"""

from floppy_server.mcp.hints import ToolHints

READ = "floppy:read"
WRITE = "floppy:write"
ADMIN = "floppy:admin"

# Widest first: `admin` implies `write`, which implies `read`.
_HIERARCHY = (ADMIN, WRITE, READ)

ALL_SCOPES = (READ, WRITE, ADMIN)

# Every request to the surface needs at least reading: listing the tools is a read.
REQUIRED_SCOPES = (READ,)

# What clients are told to ask for: everything, because there is no step-up (a client told
# only `read` would never get a chance to ask for more). The consent is where a person
# narrows it.
ADVERTISED_SCOPES = ALL_SCOPES


def scope_for(hints: ToolHints) -> str:
    """The narrowest scope a tool with these hints needs: destructive wins, only read-only reads."""
    if hints.destructive:
        return ADMIN
    if hints.read_only:
        return READ
    return WRITE


def expand(granted: list[str]) -> list[str]:
    """The granted scopes with what they imply, widest first; unknown scopes are dropped."""
    known = [scope for scope in _HIERARCHY if scope in granted]
    if not known:
        return []
    return list(_HIERARCHY[_HIERARCHY.index(known[0]) :])


def covers(granted: list[str], required: str) -> bool:
    """Whether the granted scopes include `required`, hierarchy included."""
    return required in expand(granted)
