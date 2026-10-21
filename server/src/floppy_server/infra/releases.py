"""
The app's latest release, read from its public releases (GitHub's `releases/latest` by
default), so devices are flagged `appOutdated` without a setting bumped at every release.

The control plane's background loop fetches it every few hours and keeps it in memory on the
context; `FLOPPY_LATEST_APP_VERSION` stays as the floor, so the effective latest is the higher
of the two, and an instance without internet sets the endpoint empty and lives on the floor.
"""

import re

import httpx

FETCH_TIMEOUT_SECONDS = 5.0
USER_AGENT = "floppy-server (release check)"

_PLAIN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class ReleaseLookupError(Exception):
    """The latest release could not be read; the message never carries a secret."""


def version_tuple(version: str) -> tuple[int, ...]:
    """A dotted version as numbers, ignoring anything after the first non-numeric part."""
    parts: list[int] = []
    for piece in version.strip().lstrip("v").split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def parse_release_tag(tag: str) -> str | None:
    """`v0.2.1` or `0.2.1` as `0.2.1`; anything that is not a plain X.Y.Z is None."""
    match = _PLAIN.fullmatch(tag.strip())
    if match is None:
        return None
    return ".".join(str(int(part)) for part in match.groups())


def effective_app_version(floor: str, fetched: str | None) -> str:
    """The latest app release: the fetched one when higher than the configured floor."""
    if fetched is not None and version_tuple(fetched) > version_tuple(floor):
        return fetched
    return floor


async def fetch_latest_app_version(
    url: str,
    *,
    timeout_seconds: float = FETCH_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The version of the latest release at `url`, a GitHub `releases/latest` endpoint."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"user-agent": USER_AGENT, "accept": "application/vnd.github+json"},
            transport=transport,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        raise ReleaseLookupError(f"unreachable: {type(error).__name__}") from error
    if response.status_code != 200:
        raise ReleaseLookupError(f"answered {response.status_code}")
    try:
        tag = response.json()["tag_name"]
    except (ValueError, KeyError, TypeError) as error:
        raise ReleaseLookupError("answered without a tag_name") from error
    if not isinstance(tag, str):
        raise ReleaseLookupError("answered without a tag_name")
    version = parse_release_tag(tag)
    if version is None:
        raise ReleaseLookupError(f"the tag {tag[:40]!r} is not a plain X.Y.Z")
    return version
