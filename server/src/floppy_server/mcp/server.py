"""
The MCP server (revision 2026-07-28, and the 2025 ones from the same endpoint), mounted at
`/mcp/` behind the OAuth gate (oauth/resource.py).

Every call resolves the person from its token: the membership's role is read again, so a
role change or a removal applies to the next call. `tools/list` shows only what the role may
call; `tools/call` also checks the token's scope against the tool's hints. A refusal from a
service (`ApiError`) is a tool result with `isError`, which the model reads and corrects;
only a tool outside the surface is a protocol error.

Two things the host application must do: run `session_manager.run()` in its lifespan, and
mount `asgi_app` under `/mcp` (the internal path is `/`, so the public one stays `/mcp/`).
"""

import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from mcp import types
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.caching import CacheableMethod, CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from starlette.applications import Starlette

from floppy_server.api import Actor
from floppy_server.context import Context
from floppy_server.errors import ApiError
from floppy_server.mcp.tools import BY_NAME, Tool, visible_to
from floppy_server.oauth import scopes
from floppy_server.oauth.provider import live_member

log = logging.getLogger(__name__)

VERSION = "0.1.0"

INSTRUCTIONS = """\
Floppy is a shared remote disk for teams on macOS: each disk is a folder in /Volumes/Floppy \
on every Mac that may see it, and the files live on S3 storage. These tools administer the \
team: disks, people, who sees which disk, Macs, the storage keys and the audit log. Call \
`whoami` first: an admin sees every tool, a member only their own disks, activity and \
devices. Operations on this Mac's copies (make available offline, free up space, trash) are \
not here: they are on the local `floppy` MCP server of the Floppy app.\
"""

# The list changes only with a deploy or a role change: five minutes. Private: the list
# depends on who asks.
CACHE_HINTS: Mapping[CacheableMethod, CacheHint] = {
    "server/discover": CacheHint(ttl_ms=3_600_000, scope="private"),
    "tools/list": CacheHint(ttl_ms=300_000, scope="private"),
}


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """A JSON Schema with its `$defs` inlined: some clients do not follow references."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return resolve(dict(defs[ref.removeprefix("#/$defs/")]))
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    resolved.pop("title", None)
    return resolved


def definition(tool: Tool) -> types.Tool:
    """A tool as `tools/list` describes it."""
    return types.Tool(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_schema=_inline(tool.args.model_json_schema()),
        annotations=types.ToolAnnotations(
            title=tool.title,
            read_only_hint=tool.hints.read_only,
            destructive_hint=tool.hints.destructive,
            idempotent_hint=tool.hints.idempotent,
            open_world_hint=tool.hints.open_world,
        ),
    )


def _result(payload: object, *, error: bool = False) -> types.CallToolResult:
    data = to_jsonable_python(payload, by_alias=True)
    text = json.dumps(data, ensure_ascii=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=data if isinstance(data, dict) else {"result": data},
        is_error=error,
    )


def _error(
    code: str, message: str, details: Mapping[str, object] | None = None
) -> types.CallToolResult:
    return _result({"code": code, "message": message, "details": dict(details or {})}, error=True)


class FloppyMcp:
    """The server, its transport and the handlers, around the process's context."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.server: Server[Any] = Server(
            name="floppy",
            version=VERSION,
            title="Floppy",
            instructions=INSTRUCTIONS,
            cache_hints=CACHE_HINTS,
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )
        self.asgi_app: Starlette = self.server.streamable_http_app(
            streamable_http_path="/",
            json_response=True,
            stateless_http=True,
            transport_security=self._transport_security(),
        )

    @property
    def session_manager(self) -> Any:
        """The transport's session manager, to `run()` in the lifespan."""
        return self.server.session_manager

    def _transport_security(self) -> TransportSecuritySettings:
        # The SDK's DNS rebinding protection allows only localhost by default: behind the
        # real host every request would be a silent 421. The bearer is the real boundary.
        netloc = urlsplit(self.ctx.settings.public_url).netloc
        hosts = ["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*", netloc, f"{netloc}:*"]
        origins = [self.ctx.settings.public_origin, "http://localhost", "http://127.0.0.1"]
        return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)

    async def actor(self) -> tuple[Actor, list[str]] | None:
        """The person the call's token acts as, with the token's scopes; None when gone."""
        token = get_access_token()
        if token is None or token.subject is None or not token.claims:
            return None
        organization_id = UUID(str(token.claims["organization_id"]))
        async with self.ctx.sessions() as db:
            member = await live_member(db, organization_id, UUID(token.subject))
        if member is None:
            return None
        membership, user = member
        actor = Actor(
            session_id=UUID(str(token.claims["grant_id"])),
            user_id=user.id,
            membership_id=membership.id,
            organization_id=organization_id,
            role=membership.role,
            email=user.email,
            name=user.name,
        )
        return actor, list(token.scopes)

    async def list_tools(
        self, _ctx: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        """The tools the token's person may call, in a fixed order (it keeps clients' caches)."""
        resolved = await self.actor()
        if resolved is None:
            return types.ListToolsResult(tools=[])
        actor, granted = resolved
        return types.ListToolsResult(
            tools=[
                definition(tool)
                for tool in visible_to(actor)
                if scopes.covers(granted, scopes.scope_for(tool.hints))
            ]
        )

    async def call_tool(
        self, _ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        """Runs one tool as the token's person; refusals come back as results."""
        tool = BY_NAME.get(params.name)
        if tool is None:
            raise MCPError(types.INVALID_PARAMS, f"No such tool: {params.name}")
        resolved = await self.actor()
        if resolved is None:
            return _error(
                "unauthorized", "This connection no longer acts for anyone: sign in again"
            )
        actor, granted = resolved
        if tool.admin and not actor.is_admin:
            return _error("forbidden", "Admins only")
        needed = scopes.scope_for(tool.hints)
        if not scopes.covers(granted, needed):
            return _error(
                "insufficient_scope",
                f"This connection was not granted {needed}: reconnect and tick it on the consent page",
            )
        try:
            args = tool.args.model_validate(params.arguments or {})
        except ValidationError as error:
            fields = [
                {"loc": [str(p) for p in item["loc"]], "type": item["type"], "msg": item["msg"]}
                for item in error.errors(include_input=False)
            ]
            return _error("validation", "Invalid arguments", {"fields": fields})
        try:
            payload = await tool.run(self.ctx, actor, args)
        except ApiError as error:
            return _error(error.code, error.message, error.details)
        except Exception:
            log.exception("MCP tool %s failed", tool.name)
            return _error("server", "Internal error")
        log.info("mcp tool %s by membership %s", tool.name, actor.membership_id)
        return _result(payload)
