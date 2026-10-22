"""
How a person connects Claude to Floppy: the MCP address and commands (`GET /mcp/install`,
for the admin web's page), the skill (`GET /mcp/skill`) and a setup script for Claude Code
(`GET /mcp/setup.sh`). Public on purpose and free of secrets: the sign-in happens in the
browser, through OAuth, when the client first connects.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from floppy_server.api import CamelModel, CtxDep
from floppy_server.oauth.resource import resource_url
from floppy_server.paths import API_PREFIX

router = APIRouter(tags=["mcp"])

SKILL = Path(__file__).with_name("SKILL.md")
# Where the Floppy app's binary answers MCP on stdio (`floppy-app mcp`, app/src-tauri/src/mcp.rs).
APP_BINARY = "/Applications/Floppy.app/Contents/MacOS/floppy-app"
SERVER_NAME = "floppy"
LOCAL_SERVER_NAME = "floppy-mac"


class InstallOut(CamelModel):
    """The surface's address and the commands that connect a client to it."""

    url: str
    enabled: bool
    claude_code_command: str
    local_command: str
    setup_command: str


def _url(ctx: CtxDep) -> str:
    return resource_url(ctx.settings) + "/"


@router.get("/mcp/install", response_model=InstallOut)
async def install(ctx: CtxDep) -> InstallOut:
    """The MCP address (for claude.ai's custom connectors too) and the Claude Code commands."""
    url = _url(ctx)
    return InstallOut(
        url=url,
        enabled=ctx.settings.mcp_enabled,
        claude_code_command=f"claude mcp add --transport http --scope user {SERVER_NAME} {url}",
        local_command=f"claude mcp add --scope user {LOCAL_SERVER_NAME} -- {APP_BINARY} mcp",
        setup_command=f"curl -fsS {ctx.settings.issuer}{API_PREFIX}/mcp/setup.sh | sh",
    )


@router.get("/mcp/skill", response_class=PlainTextResponse)
async def skill() -> PlainTextResponse:
    """The Claude skill that teaches both servers, as Markdown."""
    return PlainTextResponse(SKILL.read_text(encoding="utf-8"), media_type="text/markdown")


@router.get("/mcp/setup.sh", response_class=PlainTextResponse)
async def setup_script(ctx: CtxDep) -> PlainTextResponse:
    """A POSIX script: adds both servers to Claude Code for the user and installs the skill."""
    url = _url(ctx)
    skill_url = f"{ctx.settings.issuer}{API_PREFIX}/mcp/skill"
    script = f"""#!/bin/sh
# Connects Claude Code to Floppy ({ctx.settings.issuer}): the team's server, this Mac's
# server when the Floppy app is installed, and the skill. Safe to run again.
set -eu
command -v claude >/dev/null 2>&1 || {{ echo "Claude Code is not installed: https://claude.com/claude-code" >&2; exit 1; }}
claude mcp remove --scope user {SERVER_NAME} >/dev/null 2>&1 || true
claude mcp add --transport http --scope user {SERVER_NAME} {url}
if [ -x "{APP_BINARY}" ]; then
  claude mcp remove --scope user {LOCAL_SERVER_NAME} >/dev/null 2>&1 || true
  claude mcp add --scope user {LOCAL_SERVER_NAME} -- "{APP_BINARY}" mcp
else
  echo "The Floppy app is not in /Applications: only the team's server was added."
fi
dir="$HOME/.claude/skills/floppy"
mkdir -p "$dir"
curl -fsS "{skill_url}" -o "$dir/SKILL.md.part" && mv "$dir/SKILL.md.part" "$dir/SKILL.md"
echo "Done. In Claude Code, run /mcp and choose {SERVER_NAME} to sign in."
"""
    return PlainTextResponse(script, media_type="text/x-shellscript")
