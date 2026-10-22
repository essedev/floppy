"""
The MCP surface end to end: the authorization server's metadata, the OAuth flow with the
consent in the admin web, the gate in front of `/mcp/`, and the tools as admin and member,
over HTTP against the real database.

The root client talks to `http://localhost:4508`, the test settings' public URL: the issuer,
and a host the transport's DNS rebinding protection accepts. The app runs its lifespan, which
starts the MCP session manager.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.app import create_app
from floppy_server.context import Context, utcnow
from floppy_server.mcp.server import definition
from floppy_server.mcp.tools import TOOLS
from floppy_server.models import AuditEvent
from floppy_server.oauth import cimd
from floppy_server.oauth.client import FloppyClient
from tests.conftest import Org
from tests.helpers import invite_and_join, pkce, sign_in

ISSUER = "http://localhost:4508"
RESOURCE = f"{ISSUER}/mcp"
ALL = "floppy:read floppy:write floppy:admin"
REDIRECT = "http://localhost/callback"


@dataclass
class Stack:
    """The app with its lifespan running, a root client and a factory of API clients."""

    root: httpx.AsyncClient
    api: Callable[[], httpx.AsyncClient]


@pytest.fixture
async def stack(ctx: Context) -> AsyncIterator[Stack]:
    app = create_app(ctx)
    transport = httpx.ASGITransport(app=app)
    made: list[httpx.AsyncClient] = []

    def api() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver/api/v1")
        made.append(client)
        return client

    # The lifespan runs in a task of its own: the MCP session manager's task group must be
    # entered and left by the same task, and a fixture's setup and teardown are not.
    started, stop = asyncio.Event(), asyncio.Event()

    async def lifespan() -> None:
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await started.wait()
    async with httpx.AsyncClient(transport=transport, base_url=ISSUER) as root:
        yield Stack(root=root, api=api)
    for client in made:
        await client.aclose()
    stop.set()
    await task


async def register(root: httpx.AsyncClient, name: str = "Claude Code") -> str:
    response = await root.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "client_name": name,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert response.status_code == 201, response.text
    client_id: str = response.json()["client_id"]
    return client_id


@dataclass
class Started:
    request_id: str
    verifier: str
    redirect_uri: str


async def start(
    root: httpx.AsyncClient,
    client_id: str,
    *,
    port: int = 53114,
    scope: str = ALL,
    resource: str | None = RESOURCE,
) -> Started:
    verifier, challenge = pkce()
    redirect_uri = f"http://localhost:{port}/callback"
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "the-state",
        "scope": scope,
    }
    if resource is not None:
        params["resource"] = resource
    response = await root.get("/authorize", params=params)
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/autorizza"
    return Started(parse_qs(location.query)["richiesta"][0], verifier, redirect_uri)


async def approve(web: httpx.AsyncClient, request_id: str, scopes: list[str]) -> str:
    response = await web.post(f"/oauth/requests/{request_id}/approve", json={"scopes": scopes})
    assert response.status_code == 200, response.text
    target = urlsplit(response.json()["redirectTo"])
    query = parse_qs(target.query)
    assert query["state"] == ["the-state"]
    assert query["iss"] == [ISSUER]
    code: str = query["code"][0]
    return code


async def exchange(
    root: httpx.AsyncClient, client_id: str, started: Started, code: str
) -> httpx.Response:
    return await root.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": started.redirect_uri,
            "client_id": client_id,
            "code_verifier": started.verifier,
            "resource": RESOURCE,
        },
    )


async def connect(
    stack: Stack, web: httpx.AsyncClient, scopes: list[str] | None = None
) -> dict[str, Any]:
    """A client registered, approved by `web`'s person, and its tokens."""
    client_id = await register(stack.root)
    started = await start(stack.root, client_id)
    code = await approve(web, started.request_id, scopes if scopes is not None else ALL.split())
    response = await exchange(stack.root, client_id, started, code)
    assert response.status_code == 200, response.text
    tokens: dict[str, Any] = response.json()
    tokens["client_id"] = client_id
    return tokens


class Mcp:
    """A tiny JSON-RPC client for `/mcp/`."""

    def __init__(self, root: httpx.AsyncClient, token: str) -> None:
        self.root = root
        self.token = token
        self.next_id = 0

    async def raw(self, method: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self.next_id += 1
        return await self.root.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}},
        )

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.raw(method, params)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        assert "error" not in body, body
        result: dict[str, Any] = body["result"]
        return result

    async def tools(self) -> list[str]:
        return [str(t["name"]) for t in (await self.rpc("tools/list"))["tools"]]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, Any]:
        result = await self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return bool(result.get("isError")), json.loads(result["content"][0]["text"])


async def initialized(root: httpx.AsyncClient, token: str) -> Mcp:
    client = Mcp(root, token)
    await client.rpc(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )
    return client


# Metadata and the gate


async def test_authorization_server_metadata_announces_cimd_and_public_clients(
    stack: Stack,
) -> None:
    body = (await stack.root.get("/.well-known/oauth-authorization-server")).json()
    assert body["issuer"] == ISSUER
    assert body["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert body["client_id_metadata_document_supported"] is True
    assert body["authorization_response_iss_parameter_supported"] is True
    assert body["token_endpoint_auth_methods_supported"][0] == "none"
    assert set(body["scopes_supported"]) == set(ALL.split())


async def test_protected_resource_metadata_names_the_issuer(stack: Stack) -> None:
    body = (await stack.root.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]


async def test_mcp_without_a_token_challenges_with_the_metadata(stack: Stack) -> None:
    response = await stack.root.post("/mcp/", json={})
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert f'resource_metadata="{ISSUER}/.well-known/oauth-protected-resource/mcp"' in challenge
    assert "floppy:admin" in challenge


async def test_an_invented_token_is_refused(stack: Stack) -> None:
    response = await Mcp(stack.root, "invented").raw("tools/list")
    assert response.status_code == 401


async def test_bare_mcp_redirects_keeping_the_method(stack: Stack) -> None:
    response = await stack.root.post("/mcp", json={})
    assert response.status_code == 307
    assert response.headers["location"] == "/mcp/"


# The flow


async def test_admin_connects_and_sees_every_tool(stack: Stack, ctx: Context, org: Org) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    tokens = await connect(stack, web)
    assert tokens["token_type"] == "Bearer"
    assert set(tokens["scope"].split()) == set(ALL.split())
    mcp = await initialized(stack.root, tokens["access_token"])
    assert await mcp.tools() == [t.name for t in TOOLS]
    error, me = await mcp.call("whoami")
    assert not error
    assert me["role"] == "admin"
    assert me["person"]["email"] == org.admin_email
    error, disks = await mcp.call("list_disks")
    assert not error
    assert disks == []


async def test_the_consent_shows_client_and_scopes(stack: Stack, ctx: Context, org: Org) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    client_id = await register(stack.root, "Claude Code")
    started = await start(stack.root, client_id)
    body = (await web.get(f"/oauth/requests/{started.request_id}")).json()
    assert body["client"]["name"] == "Claude Code"
    assert body["client"]["redirectHost"] == "localhost:53114"
    assert body["client"]["loopbackOnly"] is True
    assert body["scopes"] == ["floppy:admin", "floppy:write", "floppy:read"]
    assert body["role"] == "admin"


async def test_the_consent_needs_a_session(stack: Stack) -> None:
    client_id = await register(stack.root)
    started = await start(stack.root, client_id)
    response = await stack.api().get(f"/oauth/requests/{started.request_id}")
    assert response.status_code == 401


async def test_unchecked_scopes_narrow_the_token_and_the_tools(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    tokens = await connect(stack, web, scopes=[])
    assert tokens["scope"] == "floppy:read"
    mcp = await initialized(stack.root, tokens["access_token"])
    names = await mcp.tools()
    assert "list_disks" in names
    assert "create_disk" not in names
    assert "remove_person" not in names
    error, body = await mcp.call(
        "remove_person", {"person_id": "0190d1b6-0000-7000-8000-000000000000"}
    )
    assert error
    assert body["code"] == "insufficient_scope"


async def test_a_member_sees_only_their_tools(stack: Stack, ctx: Context, org: Org) -> None:
    admin = stack.api()
    await sign_in(admin, ctx, org.admin_email)
    member = stack.api()
    await invite_and_join(admin, member, ctx, "luca@studio-a.test")
    tokens = await connect(stack, member)
    mcp = await initialized(stack.root, tokens["access_token"])
    assert await mcp.tools() == [t.name for t in TOOLS if not t.admin]
    error, body = await mcp.call("list_people")
    assert error
    assert body["code"] == "forbidden"
    error, me = await mcp.call("whoami")
    assert not error
    assert me["role"] == "member"


async def test_deny_returns_access_denied_to_the_client(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    client_id = await register(stack.root)
    started = await start(stack.root, client_id)
    response = await web.post(f"/oauth/requests/{started.request_id}/deny")
    query = parse_qs(urlsplit(response.json()["redirectTo"]).query)
    assert query["error"] == ["access_denied"]
    again = await web.post(f"/oauth/requests/{started.request_id}/approve", json={"scopes": []})
    assert again.status_code == 410


async def test_an_unregistered_redirect_is_refused(stack: Stack) -> None:
    client_id = await register(stack.root)
    verifier, challenge = pkce()
    response = await stack.root.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://evil.test/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 400
    assert verifier


async def test_a_wrong_verifier_is_refused(stack: Stack, ctx: Context, org: Org) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    client_id = await register(stack.root)
    started = await start(stack.root, client_id)
    code = await approve(web, started.request_id, [])
    started.verifier = "x" * 64
    response = await exchange(stack.root, client_id, started, code)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_a_replayed_code_revokes_what_it_issued(stack: Stack, ctx: Context, org: Org) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    client_id = await register(stack.root)
    started = await start(stack.root, client_id)
    code = await approve(web, started.request_id, [])
    first = await exchange(stack.root, client_id, started, code)
    assert first.status_code == 200
    second = await exchange(stack.root, client_id, started, code)
    assert second.status_code == 400
    response = await Mcp(stack.root, first.json()["access_token"]).raw("tools/list")
    assert response.status_code == 401


async def test_a_token_for_another_resource_is_refused(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    client_id = await register(stack.root)
    started = await start(stack.root, client_id, resource="https://other.test/mcp")
    code = await approve(web, started.request_id, [])
    response = await stack.root.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": started.redirect_uri,
            "client_id": client_id,
            "code_verifier": started.verifier,
        },
    )
    token = response.json()["access_token"]
    refused = await Mcp(stack.root, token).raw("tools/list")
    assert refused.status_code == 401
    assert "not issued for" in refused.json()["error_description"]


async def test_refresh_rotates_both_tokens_under_one_connection(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    tokens = await connect(stack, web)
    refresh = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": tokens["client_id"],
    }
    renewed = await stack.root.post("/token", data=refresh)
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["refresh_token"] != tokens["refresh_token"]
    assert (await stack.root.post("/token", data=refresh)).status_code == 400
    old = await Mcp(stack.root, tokens["access_token"]).raw("tools/list")
    assert old.status_code == 401
    mcp = await initialized(stack.root, renewed.json()["access_token"])
    assert "whoami" in await mcp.tools()
    connections = (await web.get("/connections")).json()
    assert len(connections) == 1
    assert connections[0]["clientName"] == "Claude Code"


async def test_revoking_a_connection_stops_its_tokens(
    stack: Stack, ctx: Context, org: Org, owner_engine: AsyncEngine
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    tokens = await connect(stack, web)
    [connection] = (await web.get("/connections")).json()
    assert (await web.post(f"/connections/{connection['id']}/revoke")).status_code == 204
    assert (await web.get("/connections")).json() == []
    response = await Mcp(stack.root, tokens["access_token"]).raw("tools/list")
    assert response.status_code == 401
    async with ctx.sessions() as db:
        actions = list(
            (
                await db.execute(
                    select(AuditEvent.action)
                    .where(AuditEvent.category == "access")
                    .order_by(AuditEvent.occurred_at)
                )
            ).scalars()
        )
    assert "mcp_client_connected" in actions
    assert actions[-1] == "mcp_client_disconnected"
    assert owner_engine


async def test_another_persons_connection_cannot_be_revoked(
    stack: Stack, ctx: Context, org: Org
) -> None:
    admin = stack.api()
    await sign_in(admin, ctx, org.admin_email)
    member = stack.api()
    await invite_and_join(admin, member, ctx, "luca@studio-a.test")
    await connect(stack, admin)
    [connection] = (await admin.get("/connections")).json()
    response = await member.post(f"/connections/{connection['id']}/revoke")
    assert response.status_code == 404


async def test_the_client_revoking_its_token_ends_the_grant(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    tokens = await connect(stack, web)
    # The SDK's revocation form declares `client_secret` without a default: a public client
    # sends it empty (mcp 2.3).
    response = await stack.root.post(
        "/revoke",
        data={
            "token": tokens["refresh_token"],
            "client_id": tokens["client_id"],
            "client_secret": "",
        },
    )
    assert response.status_code == 200, response.text
    gone = await Mcp(stack.root, tokens["access_token"]).raw("tools/list")
    assert gone.status_code == 401


async def test_removing_a_person_stops_their_clients(stack: Stack, ctx: Context, org: Org) -> None:
    admin = stack.api()
    await sign_in(admin, ctx, org.admin_email)
    member = stack.api()
    person = await invite_and_join(admin, member, ctx, "luca@studio-a.test")
    tokens = await connect(stack, member)
    assert (await admin.delete(f"/people/{person['id']}")).status_code == 200
    response = await Mcp(stack.root, tokens["access_token"]).raw("tools/list")
    assert response.status_code == 401


async def test_a_role_change_applies_to_the_next_call(stack: Stack, ctx: Context, org: Org) -> None:
    admin = stack.api()
    await sign_in(admin, ctx, org.admin_email)
    member = stack.api()
    person = await invite_and_join(admin, member, ctx, "luca@studio-a.test")
    mcp = await initialized(stack.root, (await connect(stack, member))["access_token"])
    assert "list_people" not in await mcp.tools()
    await admin.patch(f"/people/{person['id']}", json={"role": "admin"})
    assert "list_people" in await mcp.tools()


# Tools


async def test_tool_errors_are_results_the_model_can_read(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    mcp = await initialized(stack.root, (await connect(stack, web))["access_token"])
    error, body = await mcp.call("get_disk", {"disk_id": "0190d1b6-0000-7000-8000-000000000000"})
    assert error
    assert body["code"] == "not_found"
    error, body = await mcp.call("get_disk", {"disk_id": "nope"})
    assert error
    assert body["code"] == "validation"
    error, body = await mcp.call("list_disks", {"unexpected": 1})
    assert error
    assert body["code"] == "validation"


async def test_an_unknown_tool_is_a_protocol_error(stack: Stack, ctx: Context, org: Org) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    mcp = await initialized(stack.root, (await connect(stack, web))["access_token"])
    body = (await mcp.raw("tools/call", {"name": "format_everything", "arguments": {}})).json()
    assert body["error"]["code"] == -32602


async def test_manual_storage_disks_are_created_in_the_admin_web(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    mcp = await initialized(stack.root, (await connect(stack, web))["access_token"])
    error, body = await mcp.call("create_disk", {"name": "Progetti"})
    assert error
    assert body["code"] == "manual_storage"


async def test_invite_and_access_through_tools(stack: Stack, ctx: Context, org: Org) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    mcp = await initialized(stack.root, (await connect(stack, web))["access_token"])
    error, person = await mcp.call(
        "invite_person", {"email": "Sara@Studio-A.test", "role": "member"}
    )
    assert not error, person
    assert person["email"] == "sara@studio-a.test"
    error, people = await mcp.call("list_people")
    assert not error
    assert {p["email"] for p in people} == {org.admin_email, "sara@studio-a.test"}


async def test_disk_activity_refuses_a_disk_the_person_does_not_see(
    stack: Stack, ctx: Context, org: Org
) -> None:
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    mcp = await initialized(stack.root, (await connect(stack, web))["access_token"])
    error, body = await mcp.call(
        "disk_activity", {"disk_id": "0190d1b6-0000-7000-8000-000000000000"}
    )
    assert error
    assert body["code"] == "not_found"


def test_every_tool_schema_is_plain_for_strict_clients() -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            assert "$ref" not in node
            if node.get("type") == "array":
                assert "items" in node
            if "enum" in node:
                assert node["enum"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    for tool in TOOLS:
        schema = definition(tool).input_schema
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        walk(schema)
        assert len(tool.description) <= 400, tool.name


# Installation


async def test_install_endpoints_name_the_address(stack: Stack) -> None:
    api = stack.api()
    body = (await api.get("/mcp/install")).json()
    assert body["url"] == f"{RESOURCE}/"
    assert body["claudeCodeCommand"].endswith(f"floppy {RESOURCE}/")
    script = (await api.get("/mcp/setup.sh")).text
    assert script.startswith("#!/bin/sh")
    assert f"floppy {RESOURCE}/" in script
    skill = await api.get("/mcp/skill")
    assert skill.status_code == 200
    assert skill.text.startswith("---\nname: floppy")


# CIMD


def test_document_urls_are_https_with_a_path() -> None:
    assert cimd.is_document_url("https://claude.ai/oauth/claude-code-client-metadata")
    assert not cimd.is_document_url("http://claude.ai/client")
    assert not cimd.is_document_url("https://claude.ai/")
    assert not cimd.is_document_url("https://claude.ai/client?x=1")
    assert not cimd.is_document_url("0190d1b6-0000-7000-8000-000000000000")


def test_private_addresses_are_not_fetched() -> None:
    assert not cimd._public({"127.0.0.1"})
    assert not cimd._public({"169.254.169.254"})
    assert not cimd._public({"10.0.0.1", "93.184.216.34"})
    assert not cimd._public(set())
    assert cimd._public({"93.184.216.34"})


def test_a_document_must_name_its_own_url() -> None:
    url = "https://client.test/meta"
    good = {"client_id": url, "client_name": "C", "redirect_uris": [REDIRECT]}
    assert cimd._validated(url, good) is not None
    assert cimd._validated(url, {**good, "client_id": "https://other.test/meta"}) is None
    assert cimd._validated(url, {"client_id": url}) is None


async def test_a_cimd_client_authorizes_without_registering(
    stack: Stack, ctx: Context, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://client.test/meta"
    fetched: list[str] = []

    async def fake_fetch(_: object, client_id: str) -> tuple[FloppyClient, Any]:
        fetched.append(client_id)
        client = FloppyClient.model_validate(
            {
                "client_id": url,
                "client_name": "Remote",
                "redirect_uris": [REDIRECT],
                "token_endpoint_auth_method": "none",
            }
        )

        return client, utcnow() + timedelta(minutes=5)

    monkeypatch.setattr(cimd, "fetch", fake_fetch)
    web = stack.api()
    await sign_in(web, ctx, org.admin_email)
    started = await start(stack.root, url)
    code = await approve(web, started.request_id, [])
    response = await exchange(stack.root, url, started, code)
    assert response.status_code == 200, response.text
    assert fetched == [url]
    async with ctx.sessions() as db:
        source = await db.scalar(
            text("SELECT source FROM oauth_clients WHERE client_id = :c"), {"c": url}
        )
    assert source == "cimd"
