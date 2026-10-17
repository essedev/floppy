"""Rate limits per client address on sign-in, codes and links; the docs off in production."""

import httpx
import pytest

from floppy_server.app import create_app
from floppy_server.config import Environment
from floppy_server.context import Context
from floppy_server.ratelimit import Limit, RateLimiter
from tests.conftest import ClientFactory, Org, make_settings


def test_the_window_slides() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    limits = (Limit(2, 60), Limit(3, 3600))
    assert limiter.hit("r", "a", limits) is None
    assert limiter.hit("r", "a", limits) is None
    assert limiter.hit("r", "a", limits) == pytest.approx(60)
    assert limiter.hit("r", "b", limits) is None  # another address
    now[0] = 61
    assert limiter.hit("r", "a", limits) is None
    # The hourly limit holds even when the minute is free again.
    now[0] = 130
    assert limiter.hit("r", "a", limits) == pytest.approx(3600 - 130)


async def test_sign_in_is_limited_per_address(
    org: Org, ctx: Context, client: httpx.AsyncClient
) -> None:
    ctx.settings.sign_in_per_minute = 3
    for n in range(3):
        response = await client.post("/auth/sign-in", json={"email": f"x{n}@else.test"})
        assert response.status_code == 200
    blocked = await client.post("/auth/sign-in", json={"email": org.admin_email})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.json()["details"]["retryAfter"] == int(blocked.headers["retry-after"])


async def test_codes_are_limited_across_requests(
    org: Org, ctx: Context, clients: ClientFactory
) -> None:
    ctx.settings.code_per_minute = 4
    client = clients()
    started = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    url = f"/auth/sign-in/{started['requestId']}/code"
    answers = [(await client.post(url, json={"code": "000001"})).json()["code"] for _ in range(5)]
    # The fifth wrong code would lock the request; the address limit answers first.
    assert answers == ["invalid_code"] * 4 + ["rate_limited"]


async def test_a_limit_at_zero_is_off(org: Org, ctx: Context, client: httpx.AsyncClient) -> None:
    ctx.settings.link_per_minute = 0
    for _ in range(40):
        response = await client.post("/auth/magic-link", json={"token": "x" * 20})
        assert response.status_code == 410


async def test_openapi_is_off_in_production(ctx: Context) -> None:
    ctx.settings = make_settings(
        env=Environment.PRODUCTION,
        metadata_backend="real",
        juicefs_backend="real",
        storage_backend="real",
    )
    app = create_app(ctx)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver/api/v1"
    ) as client:
        assert (await client.get("/openapi.json")).status_code == 404
        assert (await client.get("/docs")).status_code == 404
