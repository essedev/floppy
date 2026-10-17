"""
`floppy-server`: the control plane's command line.

    floppy-server migrate                       # alembic upgrade head, as the owner role
    floppy-server bootstrap --from-env [--if-needed]
    floppy-server serve [--host H] [--port P]
    floppy-server reconcile                     # one pass of housekeeping and the ACL reconciler
    floppy-server reencrypt                     # re-seal stored secrets with the newest key

Secrets never come from arguments, which `ps` shows: the bootstrap's S3 secret key is read
from `FLOPPY_BOOTSTRAP_S3_SECRET_KEY` or, with `--secret-key-stdin`, from standard input.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from floppy_server.app import build_context, create_app
from floppy_server.background import poll_all, reconcile_all
from floppy_server.bootstrap import BootstrapError, BootstrapInput, bootstrap, organization_count
from floppy_server.config import Settings
from floppy_server.keys.service import run_rotations
from floppy_server.rekey import reencrypt_all

log = logging.getLogger("floppy_server")

SERVER_ROOT = Path(__file__).resolve().parents[2]


def _settings() -> Settings:
    return Settings()


def migrate(settings: Settings) -> None:
    """Applies every migration as the owner role."""

    config = Config(str(SERVER_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_ROOT / "migrations"))
    config.attributes["database_url"] = settings.migration_url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def _env(name: str, *, required: bool = True) -> str:
    value = os.environ.get(f"FLOPPY_BOOTSTRAP_{name}", "").strip()
    if required and not value:
        raise SystemExit(f"FLOPPY_BOOTSTRAP_{name} is not set")
    return value


async def _bootstrap(settings: Settings, args: argparse.Namespace) -> int:

    ctx = build_context(settings)
    try:
        if args.if_needed and await organization_count(ctx) > 0:
            log.info("bootstrap: the organization exists already")
            return 0
        mode = _env("STORAGE_MODE", required=False) or "manual"
        manual = mode == "manual"
        secret = ""
        if manual:
            secret = (
                sys.stdin.readline().strip() if args.secret_key_stdin else _env("S3_SECRET_KEY")
            )
        admin_credentials = _env("STORAGE_ADMIN_CREDENTIALS", required=False)
        data = BootstrapInput(
            organization_name=_env("ORGANIZATION_NAME"),
            slug=_env("ORGANIZATION_SLUG"),
            admin_email=_env("ADMIN_EMAIL"),
            s3_endpoint=_env("S3_ENDPOINT"),
            s3_region=_env("S3_REGION", required=False),
            s3_bucket=_env("S3_BUCKET"),
            s3_access_key_id=_env("S3_ACCESS_KEY_ID", required=manual),
            s3_secret_key=secret,
            valkey_url=_env("VALKEY_URL"),
            valkey_admin_url=_env("VALKEY_ADMIN_URL", required=False) or None,
            valkey_databases=int(_env("VALKEY_DATABASES", required=False) or 16),
            mail_language=_env("MAIL_LANGUAGE", required=False) or "it",
            storage_mode=mode,
            storage_provider=_env("STORAGE_PROVIDER", required=False) or None,
            storage_key_scope=_env("KEY_SCOPE", required=False) or "team",
            storage_admin_credentials=json.loads(admin_credentials) if admin_credentials else None,
        )
        try:
            org_id = await bootstrap(ctx, data)
        except BootstrapError as error:
            log.error("bootstrap refused: %s", error)
            return 1
        log.info("bootstrap: organization %s created; its admin can sign in", org_id)
        return 0
    finally:
        await ctx.engine.dispose()


async def _reconcile(settings: Settings) -> int:

    ctx = build_context(settings)
    try:
        await run_rotations(ctx)
        await reconcile_all(ctx)
        await poll_all(ctx)
        return 0
    finally:
        await ctx.engine.dispose()


async def _reencrypt(settings: Settings) -> int:

    ctx = build_context(settings)
    try:
        count = await reencrypt_all(ctx)
        log.info("reencrypt: %d stored secrets now use key id %d", count, ctx.box.current_key_id)
        return 0
    finally:
        await ctx.engine.dispose()


def serve(settings: Settings, host: str, port: int) -> None:
    """Runs the API with uvicorn."""

    app = create_app(build_context(settings))
    # X-Forwarded-For is believed only from the trusted proxies: the client address is what
    # the rate limits count, and anyone can send that header.
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
        log_config=None,
    )


def main(argv: list[str] | None = None) -> int:
    """The command line's entry point."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="floppy-server", description="Floppy control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply the database migrations (owner role)")
    boot = sub.add_parser("bootstrap", help="create the first organization from FLOPPY_BOOTSTRAP_*")
    boot.add_argument(
        "--from-env", action="store_true", required=True, help="read FLOPPY_BOOTSTRAP_* variables"
    )
    boot.add_argument(
        "--if-needed", action="store_true", help="do nothing when an organization exists"
    )
    boot.add_argument(
        "--secret-key-stdin", action="store_true", help="read the S3 secret key from stdin"
    )
    run = sub.add_parser("serve", help="serve the API")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)
    sub.add_parser(
        "reconcile", help="one pass of the rotation worker, the ACL reconciler and the pollers"
    )
    sub.add_parser("reencrypt", help="re-seal stored secrets with the newest key of the ring")
    args = parser.parse_args(argv)

    settings = _settings()
    match args.command:
        case "migrate":
            migrate(settings)
            return 0
        case "bootstrap":
            return asyncio.run(_bootstrap(settings, args))
        case "serve":
            serve(settings, args.host, args.port)
            return 0
        case "reconcile":
            return asyncio.run(_reconcile(settings))
        case "reencrypt":
            return asyncio.run(_reencrypt(settings))
    return 2
