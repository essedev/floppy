"""
The gate in front of `/mcp/`: a live token, issued for this resource, with at least `read`.

It sits inside the SDK's `AuthenticationMiddleware`, which verifies the bearer through the
provider (a live membership included). The SDK's own `RequireAuthMiddleware` checks neither
the audience (RFC 8707: a token issued for another resource must not work here) nor puts
the scopes in the challenge; this one does both. The audience comparison forgives only a
trailing slash.
"""

import json

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.types import ASGIApp, Receive, Scope, Send

from floppy_server.config import Settings
from floppy_server.oauth import scopes

RESOURCE_PATH = "/mcp"
METADATA_PATH = f"/.well-known/oauth-protected-resource{RESOURCE_PATH}"


def resource_url(settings: Settings) -> str:
    """The MCP surface's canonical URI: the issuer plus `/mcp`, the audience of its tokens."""
    return f"{settings.issuer}{RESOURCE_PATH}"


def same_resource(declared: str | None, expected: str) -> bool:
    """Whether a token was issued for this resource; one without a resource was not."""
    return bool(declared) and str(declared).rstrip("/") == expected.rstrip("/")


class RequireToken:
    """ASGI gate: 401 with the metadata's address when the token is missing or wrong, 403 on scope."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self.settings.mcp_enabled:
            await self._deny(send, 404, "not_found", "The MCP surface is off on this instance")
            return
        user = scope.get("user")
        if not isinstance(user, AuthenticatedUser):
            await self._deny(send, 401, "invalid_token", "Authentication required")
            return
        expected = resource_url(self.settings)
        if not same_resource(user.access_token.resource, expected):
            await self._deny(send, 401, "invalid_token", f"Token not issued for {expected}")
            return
        missing = [s for s in scopes.REQUIRED_SCOPES if not scopes.covers(user.scopes, s)]
        if missing:
            await self._deny(
                send, 403, "insufficient_scope", f"Scopes needed: {' '.join(missing)}", missing
            )
            return
        await self.app(scope, receive, send)

    async def _deny(
        self, send: Send, status: int, error: str, description: str, wanted: list[str] | None = None
    ) -> None:
        challenge = ", ".join(
            [
                f'error="{error}"',
                f'error_description="{description}"',
                f'resource_metadata="{self.settings.issuer}{METADATA_PATH}"',
                f'scope="{" ".join(wanted or scopes.ADVERTISED_SCOPES)}"',
            ]
        )
        body = json.dumps({"error": error, "error_description": description}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        if status in (401, 403):
            headers.append((b"www-authenticate", f"Bearer {challenge}".encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
