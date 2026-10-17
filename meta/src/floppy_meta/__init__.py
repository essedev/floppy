"""
Floppy's meta service (DECISIONS #25): one valkey-server per disk inside this container, on
the control plane's request, with its data and configuration on the container's volume. The
instances that should run start again with the container; Traefik routes devices to each by
name, with routes it reads from here.
"""

import logging

import uvicorn

from floppy_meta.api import create_app
from floppy_meta.config import Settings


def main() -> None:
    """Runs the service: the API, and the instances under it."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
        access_log=False,
    )
