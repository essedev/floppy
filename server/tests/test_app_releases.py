"""The app's latest release: the tag parsed, the floor, and the lookup off or failing."""

import logging

import httpx
import pytest

from floppy_server.background import refresh_app_version
from floppy_server.context import Context
from floppy_server.infra.releases import (
    USER_AGENT,
    ReleaseLookupError,
    effective_app_version,
    fetch_latest_app_version,
    parse_release_tag,
)

URL = "https://api.github.test/repos/essedev/floppy/releases/latest"


def answering(tag: object, status: int = 200) -> httpx.MockTransport:
    """A releases endpoint answering `tag` as `tag_name`, checking GitHub's required header."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == USER_AGENT
        return httpx.Response(status, json={"tag_name": tag, "name": "Floppy"})

    return httpx.MockTransport(handler)


def never_called(request: httpx.Request) -> httpx.Response:
    """A transport handler for a lookup that must not happen."""
    raise AssertionError(f"unexpected request to {request.url}")


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v0.2.1", "0.2.1"),
        ("0.2.1", "0.2.1"),
        (" v1.10.0 ", "1.10.0"),
        ("v01.2.3", "1.2.3"),
        ("v0.2", None),
        ("v0.2.1-beta.1", None),
        ("v0.2.1.4", None),
        ("release-0.2.1", None),
        ("latest", None),
        ("", None),
    ],
)
def test_only_a_plain_version_tag_is_parsed(tag: str, version: str | None) -> None:
    assert parse_release_tag(tag) == version


def test_the_effective_latest_is_never_below_the_floor() -> None:
    assert effective_app_version("0.1.0", "0.2.1") == "0.2.1"
    assert effective_app_version("0.1.0", "0.10.0") == "0.10.0"  # numeric, not text
    assert effective_app_version("0.3.0", "0.2.1") == "0.3.0"
    assert effective_app_version("0.2.1", "0.2.1") == "0.2.1"
    assert effective_app_version("0.3.0", None) == "0.3.0"


async def test_the_fetch_reads_the_tag() -> None:
    assert await fetch_latest_app_version(URL, transport=answering("v0.2.1")) == "0.2.1"


@pytest.mark.parametrize(
    "transport",
    [
        answering("v0.2.1", status=403),
        answering("v0.3.0-rc.1"),
        answering(None),
        httpx.MockTransport(lambda _: httpx.Response(200, text="<html>")),
        httpx.MockTransport(lambda _: httpx.Response(200, json=["v0.2.1"])),
    ],
    ids=["rate-limited", "not-plain", "no-tag", "not-json", "not-an-object"],
)
async def test_an_unusable_answer_is_a_lookup_error(transport: httpx.MockTransport) -> None:
    with pytest.raises(ReleaseLookupError):
        await fetch_latest_app_version(URL, transport=transport)


async def test_a_newer_release_raises_the_latest(ctx: Context) -> None:
    ctx.settings.app_releases_url = URL
    assert ctx.latest_app_version == "0.4.0"  # the tests' floor
    await refresh_app_version(ctx, transport=answering("v0.5.2"))
    assert ctx.latest_app_version == "0.5.2"


async def test_an_older_release_leaves_the_floor(ctx: Context) -> None:
    ctx.settings.app_releases_url = URL
    await refresh_app_version(ctx, transport=answering("v0.2.1"))
    assert ctx.fetched_app_version == "0.2.1"
    assert ctx.latest_app_version == "0.4.0"


async def test_the_lookup_turned_off_reads_nothing(ctx: Context) -> None:
    ctx.settings.app_releases_url = ""
    await refresh_app_version(ctx, transport=httpx.MockTransport(never_called))
    assert ctx.fetched_app_version is None
    assert ctx.latest_app_version == "0.4.0"


async def test_a_failed_lookup_keeps_the_last_value_with_one_warning(
    ctx: Context, caplog: pytest.LogCaptureFixture
) -> None:
    ctx.settings.app_releases_url = URL
    await refresh_app_version(ctx, transport=answering("v0.5.2"))

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with caplog.at_level(logging.WARNING, logger="floppy_server.background"):
        await refresh_app_version(ctx, transport=httpx.MockTransport(unreachable))
        await refresh_app_version(ctx, transport=answering("nightly"))
    assert ctx.latest_app_version == "0.5.2"
    warnings = [r for r in caplog.records if r.name == "floppy_server.background"]
    assert [r.levelno for r in warnings] == [logging.WARNING, logging.WARNING]
    assert "ConnectTimeout" in warnings[0].getMessage()
    assert all(r.exc_info is None for r in warnings)  # one line, no traceback
