"""The parts that need no valkey-server: settings, the store, the rendered config, the routes."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from floppy_meta.routes import device_url, traefik_config
from floppy_meta.store import Store, StoreError, new_label
from tests.conftest import ROUTES_TOKEN, TOKEN, make_settings


def test_settings_refuse_short_equal_tokens_and_bad_domains(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        make_settings(tmp_path, token="short")
    with pytest.raises(ValidationError, match="must differ"):
        make_settings(tmp_path, routes_token=TOKEN)
    with pytest.raises(ValidationError, match="host name"):
        make_settings(tmp_path, domain="Meta Example")
    with pytest.raises(ValidationError, match="inside the instances"):
        make_settings(tmp_path, listen_port=7401)
    assert make_settings(tmp_path, domain="Meta.Example.com.").domain == "meta.example.com"
    assert ROUTES_TOKEN != TOKEN


def test_labels_are_opaque_dns_labels() -> None:
    labels = {new_label() for _ in range(200)}
    assert len(labels) == 200
    assert all(re.fullmatch(r"[a-z2-7]{16}", label) for label in labels)


def test_the_store_allocates_ports_and_refuses_names_and_a_full_range(tmp_path: Path) -> None:
    store = Store(make_settings(tmp_path))
    a = store.create("disk-a", None)
    b = store.create("disk-b", 64)
    assert (a.port, b.port) == (7400, 7401)
    assert a.label != b.label and a.admin_password != b.admin_password
    assert store.get("disk-b") == b
    assert [i.name for i in store.load_all()] == ["disk-a", "disk-b"]
    for bad in ("../etc", "", "a/b", "x" * 65, ".hidden"):
        with pytest.raises(StoreError):
            store.directory(bad)
    store.create("disk-c", None)
    with pytest.raises(StoreError, match="no free port"):
        store.create("disk-d", None)
    # A discarded instance leaves its port and its data in the trash, never deleted.
    target = store.discard("disk-a")
    assert (target / "instance.json").is_file()
    assert store.get("disk-a") is None
    assert store.create("disk-d", None).port == 7400
    assert "admin_password" not in repr(b) and b.admin_password not in repr(b)


def test_records_and_acl_files_are_private_and_hold_only_the_hash(tmp_path: Path) -> None:
    store = Store(make_settings(tmp_path))
    instance = store.create("disk-a", None)
    directory = store.directory("disk-a")
    assert (directory.stat().st_mode & 0o777) == 0o700
    assert ((directory / "instance.json").stat().st_mode & 0o777) == 0o600
    acl = (directory / "users.acl").read_text()
    assert instance.admin_password not in acl
    assert re.fullmatch(r"user default on #[0-9a-f]{64} ~\* &\* \+@all\n", acl)


def test_the_config_is_fit_for_metadata(tmp_path: Path) -> None:
    store = Store(make_settings(tmp_path, appendfsync="always", repl_backlog_mb=256))
    instance = store.create("disk-a", 512)
    text = store.render_config(instance).read_text()
    for line in (
        "port 7400",
        "appendonly yes",
        "appendfsync always",
        "maxmemory-policy noeviction",
        "maxmemory 512mb",
        "repl-diskless-sync-delay 0",
        "repl-backlog-size 256mb",
        "databases 2",
        f"aclfile {store.directory('disk-a') / 'users.acl'}",
    ):
        assert line in text.splitlines()
    assert "requirepass" not in text  # the admin lives in the ACL file only


def test_routes_cover_the_running_instances_by_name_on_the_https_entrypoint(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, cert_resolver="floppy-dns")
    store = Store(settings)
    a = store.create("disk-a", None)
    b = store.create("disk-b", None)
    b.desired = "stopped"
    assert traefik_config(settings, []) == {}
    config = traefik_config(settings, [a, b])
    routers = config["tcp"]["routers"]
    assert list(routers) == [f"floppy-meta-{a.label}"]
    router = routers[f"floppy-meta-{a.label}"]
    assert router["entryPoints"] == ["websecure"]
    assert router["rule"] == f"HostSNI(`{a.label}.meta.floppy.test`)"
    assert router["tls"] == {
        "certResolver": "floppy-dns",
        "domains": [{"main": "*.meta.floppy.test"}],
    }
    service = config["tcp"]["services"][f"floppy-meta-{a.label}"]
    assert service == {"loadBalancer": {"servers": [{"address": "floppy-meta:7400"}]}}
    assert device_url(settings, a) == f"rediss://{a.label}.meta.floppy.test:443"
    # The disk's name never reaches a host name or a route.
    assert "disk-a" not in str(config)
    # Without a resolver the TLS store's default certificate serves.
    plain = traefik_config(make_settings(tmp_path), [a])
    assert plain["tcp"]["routers"][f"floppy-meta-{a.label}"]["tls"] == {}
