"""
Configuration from the environment.

Every setting is read from a `FLOPPY_` variable once, at startup, and validated here, so a
missing secret or an impossible combination stops the process before it serves anything.
Secrets (the encryption key ring, SMTP and Valkey admin passwords) are `SecretStr`, which
keeps them out of reprs and logs.
"""

import base64
import binascii
from datetime import timedelta
from enum import StrEnum
from functools import cached_property
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from floppy_server.ratelimit import Limit


class Environment(StrEnum):
    """Where the process runs: production refuses the development-only backends."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class MailBackend(StrEnum):
    """How mail leaves: an SMTP server, or the console in development."""

    SMTP = "smtp"
    CONSOLE = "console"


class Backend(StrEnum):
    """An external system reached for real, or its in-process fake."""

    REAL = "real"
    FAKE = "fake"


def parse_key_ring(raw: str) -> dict[int, bytes]:
    """
    Parses `FLOPPY_SECRET_KEYS`: comma-separated `id:base64` pairs, each key 32 bytes.
    Ids are 1 to 255; the highest one encrypts new values.
    """
    ring: dict[int, bytes] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        key_id_text, sep, encoded = item.partition(":")
        if not sep or not key_id_text.isdigit():
            raise ValueError("FLOPPY_SECRET_KEYS entries must look like '<id>:<base64 key>'")
        key_id = int(key_id_text)
        if not 1 <= key_id <= 255:
            raise ValueError("FLOPPY_SECRET_KEYS ids must be between 1 and 255")
        ring[key_id] = decode_key(encoded)
    if not ring:
        raise ValueError("FLOPPY_SECRET_KEYS is empty")
    return ring


def decode_key(encoded: str) -> bytes:
    """A 32-byte key from standard or URL-safe base64."""
    text = encoded.strip()
    try:
        key = base64.b64decode(
            text + "=" * (-len(text) % 4), altchars=b"-_" if "-" in text or "_" in text else None
        )
    except binascii.Error as error:
        raise ValueError("the secret key is not valid base64") from error
    if len(key) != 32:
        raise ValueError("the secret key must be 32 bytes (base64 of 32 random bytes)")
    return key


class Settings(BaseSettings):
    """Every setting of the control plane."""

    model_config = SettingsConfigDict(env_prefix="FLOPPY_", env_file=None, extra="ignore")

    env: Environment = Environment.PRODUCTION

    # PostgreSQL. The application connects as the role without DELETE; migrations run as
    # the owner (`migration_database_url`, falling back to `database_url`).
    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    db_app_role: str = "floppy_app"
    db_pool_size: int = 10

    # Encryption of stored secrets (AES-256-GCM) and the HMAC key for 6-digit codes.
    secret_key: SecretStr | None = None
    secret_keys: SecretStr | None = None

    # The admin web's address: links in mail point here, and unsafe requests must come
    # from this origin.
    public_url: str = "http://localhost:4508"
    cookie_secure: bool = True
    session_idle_days: int = 14
    session_absolute_days: int = 30

    # Mail. Organizations may override the SMTP server in their settings.
    mail_backend: MailBackend = MailBackend.SMTP
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_sender: str = "Floppy <floppy@localhost>"
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = 15.0

    # External systems: real, or in-process fakes for development and tests.
    metadata_backend: Backend = Backend.REAL
    juicefs_backend: Backend = Backend.REAL
    storage_backend: Backend = Backend.REAL
    # The JuiceFS binary (the image has it at /usr/local/bin/juicefs), or a command prefix
    # that runs it elsewhere, split like a shell would.
    juicefs_command: str = "juicefs"
    # The instance's Garage admin API, for managed organizations without their own.
    garage_admin_url: str | None = None
    garage_admin_token: SecretStr | None = None
    juicefs_timeout_seconds: float = 120.0
    storage_timeout_seconds: float = 15.0

    # The meta service that gives each disk its own Valkey instance (DECISIONS #25):
    # `service` is the meta service at `meta_url` with its bearer token (`meta_admin_host`
    # when the control plane reaches the instances by another name than the API's host);
    # `pool` hands out a fixed list of instances (`meta_pool`, comma-separated `url` or
    # `url|admin_url`), all with the admin credentials below; `fake` invents them.
    meta_backend: Literal["service", "pool", "fake"] = "pool"
    meta_url: str = ""
    meta_token: SecretStr | None = None
    meta_admin_host: str | None = None
    meta_pool: str = ""
    # The private CA the disks' metadata TLS is signed by, for devices to trust (a PEM, or a
    # path to one); unset when the certificate is public (Let's Encrypt). Not a secret.
    meta_ca_pem: str | None = None
    meta_ca_file: str | None = None
    valkey_admin_username: str | None = None
    valkey_admin_password: SecretStr | None = None

    # Self-hosted: one organization, refused after the first.
    single_organization: bool = True

    # The app release devices are compared against for `appOutdated`: the latest of the app's
    # public releases (`app_releases_url`, a GitHub `releases/latest` endpoint, read every 6
    # hours; empty turns the lookup off), never lower than `latest_app_version`, the floor.
    latest_app_version: str = "0.1.0"
    app_releases_url: str = "https://api.github.com/repos/essedev/floppy/releases/latest"

    # Background loops: rotation worker, ACL reconciler, status pollers.
    background_tasks: bool = True
    reconcile_interval_seconds: float = 120.0
    status_interval_seconds: float = 30.0
    rotation_poll_seconds: float = 5.0

    # The language of mail to an address in no organization when its Accept-Language
    # names neither Italian nor English.
    default_mail_language: Literal["it", "en"] = "it"

    # Sign-in: per address and hour, beyond which the latest request is returned unchanged.
    sign_in_hourly_limit: int = Field(default=10, ge=1)

    # Per client address (ratelimit.py); 0 turns a limit off. The client address is the
    # proxy's unless the request comes from one of `trusted_proxies` (comma-separated
    # addresses), whose X-Forwarded-For is then believed.
    sign_in_per_minute: int = Field(default=10, ge=0)
    sign_in_per_hour: int = Field(default=60, ge=0)
    code_per_minute: int = Field(default=30, ge=0)
    link_per_minute: int = Field(default=30, ge=0)
    trusted_proxies: str = "127.0.0.1"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.secret_key is None and self.secret_keys is None:
            raise ValueError(
                "set FLOPPY_SECRET_KEY (base64 of 32 random bytes) or FLOPPY_SECRET_KEYS"
            )
        _ = self.key_ring
        if self.env is Environment.PRODUCTION:
            if self.mail_backend is MailBackend.CONSOLE:
                raise ValueError(
                    "the console mail backend prints codes and links: development only"
                )
            fakes = [
                name
                for name, backend in (
                    ("metadata", self.metadata_backend),
                    ("juicefs", self.juicefs_backend),
                    ("storage", self.storage_backend),
                )
                if backend is Backend.FAKE
            ]
            if self.meta_backend == "fake":
                fakes.append("meta")
            if fakes:
                raise ValueError(f"fake backends are for development and tests only: {fakes}")
        if self.meta_ca_pem and self.meta_ca_file:
            raise ValueError("set FLOPPY_META_CA_PEM or FLOPPY_META_CA_FILE, not both")
        _ = self.meta_ca
        if self.meta_backend == "service" and (not self.meta_url or self.meta_token is None):
            raise ValueError("the meta service needs FLOPPY_META_URL and FLOPPY_META_TOKEN")
        if not self.public_url.startswith(("http://", "https://")):
            raise ValueError("FLOPPY_PUBLIC_URL must be an http(s) address")
        if self.app_releases_url and not self.app_releases_url.startswith(("http://", "https://")):
            raise ValueError("FLOPPY_APP_RELEASES_URL must be an http(s) address, or empty")
        return self

    @cached_property
    def meta_ca(self) -> str | None:
        """The PEM of the metadata TLS's private CA, read and checked once; None when public."""
        pem = self.meta_ca_pem
        if self.meta_ca_file:
            try:
                with open(self.meta_ca_file, encoding="ascii") as handle:
                    pem = handle.read()
            except (OSError, UnicodeDecodeError) as error:
                raise ValueError(
                    f"FLOPPY_META_CA_FILE cannot be read: {type(error).__name__}"
                ) from error
        if not pem or not pem.strip():
            return None
        text = pem.replace("\\n", "\n").strip() + "\n"
        if "-----BEGIN CERTIFICATE-----" not in text or "PRIVATE KEY" in text:
            raise ValueError("the metadata CA must be PEM certificates only, no private key")
        return text

    @cached_property
    def key_ring(self) -> dict[int, bytes]:
        """The encryption keys by id; the highest id encrypts."""
        ring: dict[int, bytes] = {}
        if self.secret_keys is not None:
            ring.update(parse_key_ring(self.secret_keys.get_secret_value()))
        if self.secret_key is not None:
            single = decode_key(self.secret_key.get_secret_value())
            if 1 in ring and ring[1] != single:
                raise ValueError("FLOPPY_SECRET_KEY differs from id 1 in FLOPPY_SECRET_KEYS")
            ring[1] = single
        return ring

    @property
    def rate_limits(self) -> dict[str, tuple[Limit, ...]]:
        """The limits of each rule, without the ones turned off."""
        rules = {
            "sign-in": (Limit(self.sign_in_per_minute, 60), Limit(self.sign_in_per_hour, 3600)),
            "code": (Limit(self.code_per_minute, 60),),
            "link": (Limit(self.link_per_minute, 60),),
        }
        return {
            name: kept
            for name, limits in rules.items()
            if (kept := tuple(item for item in limits if item.count > 0))
        }

    @property
    def session_cookie(self) -> str:
        """
        The session cookie's name: `__Host-` when the cookie is Secure, so no subdomain (the
        disks' `*.meta.<domain>`) can set or overwrite it; browsers refuse that prefix without
        Secure, so plain http in development keeps the plain name.
        """
        return "__Host-floppy_session" if self.cookie_secure else "floppy_session"

    @property
    def public_origin(self) -> str:
        """Scheme, host and port of `public_url`, as browsers send it in `Origin`."""
        scheme, _, rest = self.public_url.partition("://")
        return f"{scheme}://{rest.split('/', 1)[0]}"

    @property
    def session_idle(self) -> timedelta:
        return timedelta(days=self.session_idle_days)

    @property
    def session_absolute(self) -> timedelta:
        return timedelta(days=self.session_absolute_days)

    @property
    def migration_url(self) -> str:
        url = self.migration_database_url or self.database_url
        return url.get_secret_value()
