"""
Traefik's dynamic configuration for the instances (DECISIONS #25): one TCP router per
instance that should run, matched by the name in the TLS handshake on the HTTPS entrypoint,
TLS ended at Traefik, plain Valkey on the private network behind it.

Traefik polls it with its HTTP provider, so the routes follow the instances without a shared
file or the Docker socket.
"""

from typing import Any

from floppy_meta.config import Settings
from floppy_meta.store import Instance


def host_of(settings: Settings, instance: Instance) -> str:
    """The host name devices reach an instance by."""
    return f"{instance.label}.{settings.domain}"


def device_url(settings: Settings, instance: Instance) -> str:
    """The instance as devices reach it: TLS, through Traefik, with no credentials."""
    return f"rediss://{host_of(settings, instance)}:{settings.public_port}"


def traefik_config(settings: Settings, instances: list[Instance]) -> dict[str, Any]:
    """The TCP routers and services of every instance that should run."""
    tls: dict[str, Any] = {}
    if settings.cert_resolver:
        tls = {
            "certResolver": settings.cert_resolver,
            # One wildcard for every instance: one certificate to obtain and renew, and no
            # instance's name in certificate transparency logs.
            "domains": [{"main": f"*.{settings.domain}"}],
        }
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    for instance in instances:
        if instance.desired != "running":
            continue
        key = f"{settings.router_prefix}-{instance.label}"
        routers[key] = {
            "entryPoints": [settings.entrypoint],
            "rule": f"HostSNI(`{host_of(settings, instance)}`)",
            "service": key,
            "tls": tls,
        }
        services[key] = {
            "loadBalancer": {"servers": [{"address": f"{settings.upstream_host}:{instance.port}"}]}
        }
    if not routers:
        # No instance: an empty configuration, which Traefik takes as no routes.
        return {}
    return {"tcp": {"routers": routers, "services": services}}
