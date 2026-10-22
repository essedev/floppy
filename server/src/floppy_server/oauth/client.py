"""
The SDK's OAuth client with the two rules it lacks: the loopback port exception for redirect
URIs, and the advertised scopes for a client that declares none.

OAuth 2.1 #2.3.1: redirect URIs match exactly, except a loopback one, whose port may differ.
A native client (Claude Code) listens on a port the system picks when it needs one, so it
registers `http://localhost/callback` and comes back on `http://localhost:53114/callback`;
with the SDK's string comparison that is `invalid_request` and no sign-in ever starts.
Scheme, host, path and query still match exactly, and PKCE protects the code if another
local application took the port.
"""

from typing import Self
from urllib.parse import urlsplit

from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from pydantic import AnyUrl, model_validator

from floppy_server.oauth.scopes import ADVERTISED_SCOPES

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def is_loopback(url: str) -> bool:
    """Whether a URL points at the client machine's loopback interface."""
    return (urlsplit(url).hostname or "").lower() in LOOPBACK_HOSTS


def _without_port(url: str) -> tuple[str, str, str, str, str]:
    parts = urlsplit(url)
    return (
        parts.scheme.lower(),
        (parts.hostname or "").lower(),
        parts.path,
        parts.query,
        parts.fragment,
    )


class FloppyClient(OAuthClientInformationFull):
    """An OAuth client as this authorization server reads it."""

    @model_validator(mode="after")
    def _default_scope(self) -> Self:
        # A CIMD document need not list scopes, and the SDK checks a request against the
        # client's: without this every CIMD client would get `invalid_scope`.
        if not (self.scope or "").strip():
            self.scope = " ".join(ADVERTISED_SCOPES)
        return self

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        """The SDK's exact match first, then the same loopback URI with another port."""
        try:
            return super().validate_redirect_uri(redirect_uri)
        except InvalidRedirectUriError:
            if redirect_uri is None:
                raise
        requested = str(redirect_uri)
        if is_loopback(requested):
            wanted = _without_port(requested)
            for registered in self.redirect_uris or []:
                if is_loopback(str(registered)) and _without_port(str(registered)) == wanted:
                    return redirect_uri
        raise InvalidRedirectUriError(f"Redirect URI '{requested}' not registered for client")


def loopback_only(client: OAuthClientInformationFull) -> bool:
    """Whether every redirect of the client is on loopback: any local app could claim them."""
    uris = [str(u) for u in client.redirect_uris or []]
    return bool(uris) and all(is_loopback(u) for u in uris)
