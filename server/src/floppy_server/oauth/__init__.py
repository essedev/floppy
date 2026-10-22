"""
The control plane as its own OAuth 2.1 authorization server, for MCP clients (DECISIONS #29).

The SDK (`mcp.server.auth`) carries the protocol: the authorize, token, register and revoke
routes and the bearer middleware. Here live the grant and its rules:

- `scopes`: the three scopes, read from the hints every tool declares;
- `client`: the SDK's client with the loopback port rule and the default scopes;
- `cimd`: fetching a Client ID Metadata Document, treated as SSRF;
- `provider`: the `OAuthAuthorizationServerProvider` on PostgreSQL;
- `resource`: the gate in front of `/mcp/` (audience, scope, a live membership);
- `routes`: the authorization server's routes and metadata, assembled;
- `router`: the admin web's side, the consent and the person's connections.

The consent is a page of the admin web (`/autorizza`): approving needs a signed-in person,
and the sign-in by mail already lives there.
"""
