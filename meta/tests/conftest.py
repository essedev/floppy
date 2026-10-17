"""
Fixtures: settings over a temporary data directory, and the API over a supervisor that runs
real valkey-server processes (the image has the binary; `docker run --rm floppy-meta-test`).
"""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from floppy_meta.api import create_app
from floppy_meta.config import Settings
from floppy_meta.supervisor import Supervisor

TOKEN = "control-token-" + "c" * 32
ROUTES_TOKEN = "routes-token-" + "r" * 32


def make_settings(data_dir: Path, **overrides: object) -> Settings:
    """Settings for a test: a small port range on the loopback, short timeouts."""
    values: dict[str, object] = {
        "token": TOKEN,
        "routes_token": ROUTES_TOKEN,
        "domain": "meta.floppy.test",
        "data_dir": data_dir,
        "port_min": 7400,
        "port_max": 7402,
        "upstream_host": "floppy-meta",
        "acl_save_seconds": 0.5,
        "ready_timeout_seconds": 10.0,
        "stop_timeout_seconds": 10.0,
        "repl_backlog_mb": 1,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


def needs_valkey() -> None:
    if shutil.which("valkey-server") is None:
        pytest.skip("valkey-server is not installed: run the tests in the image (meta/Dockerfile)")


class Service:
    """A running service under test: its supervisor and an API client with the token."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.supervisor = Supervisor(settings)
        self.api = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, self.supervisor)),
            base_url="http://meta",
            headers={"authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )

    async def __aenter__(self) -> Service:
        await self.supervisor.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.api.aclose()
        await self.supervisor.shutdown()


@pytest.fixture
async def service(settings: Settings) -> AsyncIterator[Service]:
    needs_valkey()
    async with Service(settings) as running:
        yield running
