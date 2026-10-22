"""
Client ID Metadata Documents: the `client_id` is an HTTPS URL and the client describes
itself in a JSON document there, instead of registering (the MCP revision 2026-07-28 prefers
it to dynamic registration). The SDK does not implement it: it plugs into
`provider.get_client`, the one place the SDK resolves a client.

This fetch is SSRF by construction: the URL comes from whoever calls `/authorize`, which is
public. What holds:

1. `https` only, with a path and no query or fragment.
2. No redirect followed: a 302 to a metadata address would skip the next check.
3. Every address the host resolves to must be public (not private, loopback, link-local,
   multicast, reserved), checked after resolving.
4. A byte cap and a short timeout; a real document is a few hundred bytes.

What does not: between our resolution and httpx's connection the DNS answer can change
(rebinding), and the number of fetches is bounded only by the `authorize` rate limit.
"""

import asyncio
import contextlib
import ipaddress
import json
import logging
import socket
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from floppy_server.config import Settings
from floppy_server.context import utcnow
from floppy_server.oauth.client import FloppyClient

log = logging.getLogger(__name__)

REQUIRED_FIELDS = ("client_id", "client_name", "redirect_uris")


def is_document_url(client_id: str) -> bool:
    """Whether a `client_id` names a document: https, a host, a path, nothing else."""
    parts = urlsplit(client_id)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and not parts.query
        and not parts.fragment
        and parts.path not in ("", "/")
    )


def _resolve(host: str) -> set[str]:
    """The addresses `host` resolves to; empty when it does not. Tests replace this."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return set()
    return {str(info[4][0]) for info in infos}


def _public(addresses: set[str]) -> bool:
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _fresh_until(settings: Settings, response: httpx.Response) -> datetime:
    """`Cache-Control: max-age`, capped by our own window; now on no-store or no-cache."""
    window = timedelta(minutes=settings.oauth_cimd_cache_minutes)
    for directive in (
        d.strip().lower() for d in response.headers.get("cache-control", "").split(",")
    ):
        if directive in ("no-store", "no-cache"):
            return utcnow()
        if directive.startswith("max-age="):
            with contextlib.suppress(ValueError):
                window = min(window, timedelta(seconds=int(directive.removeprefix("max-age="))))
    return utcnow() + window


def _validated(client_id: str, payload: Any) -> FloppyClient | None:
    """The document as a client when it has the required fields and names its own URL."""
    if not isinstance(payload, dict):
        return None
    if any(not payload.get(field) for field in REQUIRED_FIELDS):
        log.warning("cimd document without required fields: %s", client_id)
        return None
    # Anyone can host a JSON: only one that names the URL it was fetched from is that client.
    if payload["client_id"] != client_id:
        log.warning("cimd document names another client_id: %s", client_id)
        return None
    try:
        return FloppyClient.model_validate(payload)
    except ValidationError:
        log.warning("cimd document is not a valid client: %s", client_id)
        return None


async def fetch(settings: Settings, client_id: str) -> tuple[FloppyClient, datetime] | None:
    """The client a document describes and until when to trust it; None when unusable."""
    host = urlsplit(client_id).hostname or ""
    if not _public(await asyncio.to_thread(_resolve, host)):
        log.warning("cimd host is not public: %s", host)
        return None
    try:
        async with (
            httpx.AsyncClient(
                timeout=settings.oauth_cimd_timeout_seconds,
                follow_redirects=False,
                headers={"Accept": "application/json"},
            ) as http,
            http.stream("GET", client_id) as response,
        ):
            if response.status_code != 200:
                log.warning("cimd fetch answered %s: %s", response.status_code, client_id)
                return None
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > settings.oauth_cimd_max_bytes:
                    log.warning("cimd document too large: %s", client_id)
                    return None
    except httpx.HTTPError as error:
        log.warning("cimd fetch failed (%s): %s", type(error).__name__, client_id)
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    client = _validated(client_id, payload)
    if client is None:
        return None
    return client, _fresh_until(settings, response)
