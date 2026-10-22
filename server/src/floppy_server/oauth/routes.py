"""
The authorization server's routes, assembled from the SDK: metadata, authorize, token,
register, revoke and the protected resource's metadata.

The routes sit at the origin's root (`/authorize`, `/token`, ...) because the issuer is the
origin, and RFC 8414's metadata lives at `/.well-known/oauth-authorization-server`; Traefik
sends those paths to the control plane (deploy/docker-compose.yml). The metadata is served
here and not by the SDK: its model has no extra fields, and three announcements are missing:
CIMD support, the `iss` parameter (RFC 9207), and `none` as a token endpoint method, without
which Claude's clients do not pick CIMD.
"""

from typing import Any

from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from floppy_server.config import Settings
from floppy_server.oauth import scopes
from floppy_server.oauth.provider import Provider
from floppy_server.oauth.resource import resource_url

METADATA_PATH = "/.well-known/oauth-authorization-server"
# Every path the authorization server answers on, for the proxies' rules and the origin check.
ROOT_PATHS = ("/authorize", "/token", "/register", "/revoke")


def _registration() -> ClientRegistrationOptions:
    # `default_scopes` is everything: the SDK checks requests against what a client
    # registered, and with no step-up a client registered with less could never ask again.
    return ClientRegistrationOptions(
        enabled=True,
        valid_scopes=list(scopes.ALL_SCOPES),
        default_scopes=list(scopes.ADVERTISED_SCOPES),
    )


def _urls(settings: Settings) -> AuthSettings:
    # Through `AuthSettings`: it keeps an empty path empty. A bare `AnyHttpUrl` would add a
    # slash, and clients compare the issuer with `iss` as strings (RFC 9207).
    return AuthSettings(
        issuer_url=settings.issuer,
        resource_server_url=resource_url(settings),
        required_scopes=list(scopes.REQUIRED_SCOPES),
        client_registration_options=_registration(),
        revocation_options=RevocationOptions(enabled=True),
        # The audience is checked by oauth/resource.py, which also names the scopes.
        validate_token_resource=False,
    )


def metadata(settings: Settings) -> dict[str, Any]:
    """RFC 8414 metadata: the SDK's, plus CIMD, `iss` and the public client method."""
    payload = build_metadata(
        _urls(settings).issuer_url,
        None,
        _registration(),
        RevocationOptions(enabled=True),
    ).model_dump(mode="json", exclude_none=True)
    payload["client_id_metadata_document_supported"] = True
    payload["authorization_response_iss_parameter_supported"] = True
    methods = payload.get("token_endpoint_auth_methods_supported") or []
    payload["token_endpoint_auth_methods_supported"] = [
        "none",
        *(m for m in methods if m != "none"),
    ]
    return payload


def build_routes(settings: Settings, provider: Provider) -> list[Route]:
    """Every OAuth route, to register before anything else on the app."""
    urls = _urls(settings)
    payload = metadata(settings)

    async def metadata_handler(_: Request) -> Response:
        return JSONResponse(payload, headers={"Cache-Control": "public, max-age=3600"})

    routes = [
        Route(
            METADATA_PATH,
            endpoint=cors_middleware(metadata_handler, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
    ]
    routes += [
        route
        for route in create_auth_routes(
            provider=provider,
            issuer_url=urls.issuer_url,
            client_registration_options=_registration(),
            revocation_options=RevocationOptions(enabled=True),
        )
        if isinstance(route, Route) and route.path != METADATA_PATH
    ]
    if urls.resource_server_url is None:
        raise RuntimeError("the MCP resource has no URL")
    routes += create_protected_resource_routes(
        resource_url=urls.resource_server_url,
        authorization_servers=[urls.issuer_url],
        scopes_supported=list(scopes.ADVERTISED_SCOPES),
        resource_name="Floppy",
    )
    return routes
