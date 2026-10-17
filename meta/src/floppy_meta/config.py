"""The meta service's settings, from `FLOPPY_META_*` in the environment."""

import re
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A host name the instances' routes live under: lowercase labels, at least two of them.
DOMAIN = re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


class Settings(BaseSettings):
    """Everything the service reads at start; secrets as `SecretStr`, never logged."""

    model_config = SettingsConfigDict(env_prefix="FLOPPY_META_", extra="ignore")

    # The control plane's bearer token for /v1/instances; the routes token is the one
    # Traefik's HTTP provider sends, and can read the routes only.
    token: SecretStr
    routes_token: SecretStr

    # Devices reach instance `<label>` as `rediss://<label>.<domain>:<public_port>`.
    domain: str
    public_port: int = Field(default=443, ge=1, le=65535)

    # How Traefik reaches this container (its name or alias on Traefik's network), the
    # entrypoint the routes attach to (Dokploy's HTTPS one is `websecure`), the certificate
    # resolver that obtains `*.<domain>` (empty: the TLS store's default certificate) and the
    # prefix of the routers' names, unique per Floppy instance on a shared Traefik.
    upstream_host: str = "meta"
    entrypoint: str = "websecure"
    cert_resolver: str = ""
    router_prefix: str = "floppy-meta"

    # The service's own API.
    listen_host: str = "0.0.0.0"  # noqa: S104 - a container, reached on its private network
    listen_port: int = Field(default=8080, ge=1, le=65535)

    # Where the instances live, the ports they listen on inside the container, and the
    # binary that runs them.
    data_dir: Path = Path("/data")
    port_min: int = Field(default=7000, ge=1024, le=65535)
    port_max: int = Field(default=7999, ge=1024, le=65535)
    valkey_server: str = "valkey-server"

    # Durability of the metadata (docs/ARCHITECTURE.md, "Backup and resilience"): every write
    # on disk before the answer; `everysec` may lose the last second.
    appendfsync: Literal["always", "everysec"] = "always"
    repl_backlog_mb: int = Field(default=256, ge=1)
    # How often the devices' ACL users are saved to each instance's ACL file, besides at
    # every stop; the control plane's reconciler rebuilds whatever a crash loses.
    acl_save_seconds: float = Field(default=30.0, gt=0)
    # How long a new instance has to answer PING, and a stopping one to exit.
    ready_timeout_seconds: float = Field(default=30.0, gt=0)
    stop_timeout_seconds: float = Field(default=45.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("token", "routes_token"):
            if len(getattr(self, name).get_secret_value()) < 32:
                raise ValueError(f"FLOPPY_META_{name.upper()} must be at least 32 characters")
        if self.token.get_secret_value() == self.routes_token.get_secret_value():
            raise ValueError("FLOPPY_META_ROUTES_TOKEN must differ from FLOPPY_META_TOKEN")
        self.domain = self.domain.strip().lower().rstrip(".")
        if not DOMAIN.match(self.domain):
            raise ValueError("FLOPPY_META_DOMAIN must be a host name, e.g. meta.example.com")
        if self.port_min > self.port_max:
            raise ValueError("FLOPPY_META_PORT_MIN is above FLOPPY_META_PORT_MAX")
        if self.port_min <= self.listen_port <= self.port_max:
            raise ValueError("FLOPPY_META_LISTEN_PORT falls inside the instances' port range")
        return self
