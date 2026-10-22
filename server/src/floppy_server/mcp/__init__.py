"""
The MCP surface of the control plane (DECISIONS #29): the admin web's operations as tools for
an MCP client, at `/mcp/`, behind the OAuth authorization server in `oauth/`.

- `hints`: what each tool declares it does, the source of its scope;
- `tools`: the tools, each calling the router function its REST endpoint calls;
- `server`: the MCP server and its transport;
- `install`: how a person connects Claude (the address, the skill, a setup script).
"""
