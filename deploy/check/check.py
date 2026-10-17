#!/usr/bin/env python3
"""The production compose, checked locally end to end (docs/ARCHITECTURE.md, "Deploy").

Runs deploy/docker-compose.yml with deploy/check/compose.yml: Dokploy's Traefik as Dokploy
configures it plus Floppy's blocks, a self-signed certificate for floppy.test and
*.meta.floppy.test in place of the DNS challenge, Garage as S3 and a device stand-in. Then:
the web and the API on 443 by name, the HTTP redirect, the control plane creating a disk's
instance on the meta service, Traefik routing the instance by its label on the same 443, a
Valkey replica and the patched JuiceFS through it, idle connections kept, the round trip
added, the meta service restarted, and the web's CSP in a real browser. Each check prints
its measure, its threshold and PASS or FAIL.

Usage: deploy/check/check.py [--keep]   (--keep leaves the stack running)

Needs Docker, and the patched juicefs: FLOPPY_JUICEFS_DIST names a directory with
spike/juicefs-replica/build.sh's output (default: spike/juicefs-replica/dist), or set it to
`service:juicefs` to build it from the patches as Dokploy does. The browser check needs
Google Chrome and uv. Secrets of the check live in deploy/check/.state/ (outside git) and
are never printed; its volumes (floppy-deploy-check_*) are kept between runs.
"""

import argparse
import base64
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent
ROOT = DEPLOY.parent
STATE = HERE / ".state"
CERTS = STATE / "tls"
ENV = STATE / "check.env"
PROJECT = "floppy-deploy-check"
NETWORK = "floppy-check-proxy"
SUBNET = "10.250.50.0/24"
TRAEFIK_IP = "10.250.50.240"
DOMAIN = "floppy.test"
HTTPS, HTTP = 4570, 4571
ROUTES_TOKEN = "floppy-check-routes-token-not-a-secret-0001"
REPLICA_PORT = 6390
RESULTS: list[tuple[str, str, str, bool, str]] = []


SECRETS: list[str] = []


def record(name, measure, threshold, ok, note=""):
    for secret in SECRETS:
        measure, note = measure.replace(secret, "****"), note.replace(secret, "****")
    RESULTS.append((name, measure, threshold, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {measure} (threshold {threshold}) {note}".rstrip(), flush=True)


def run(*args, env=None, check=True, timeout=1800, input=None):
    """A command; its output is returned, never printed (it may hold the check's secrets)."""
    result = subprocess.run(args, capture_output=True, text=True, env={**os.environ, **(env or {})},
                            timeout=timeout, input=input)
    if check and result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-15:]
        raise RuntimeError(f"{' '.join(args[:6])} failed:\n" + "\n".join(tail))
    return result


def dc(*args, **kw):
    return run("docker", "compose", "-p", PROJECT, "--env-file", str(ENV), "-f", str(DEPLOY / "docker-compose.yml"),
               "-f", str(HERE / "compose.yml"), *args, **kw)


def in_device(script, env=None, check=True, timeout=600):
    flags = [x for name in (env or {}) for x in ("-e", name)]
    return dc("exec", "-T", *flags, "device", "sh", "-c", script, env=env, check=check, timeout=timeout)


def in_server(code, env=None):
    flags = [x for name in (env or {}) for x in ("-e", name)]
    out = dc("exec", "-T", *flags, "server", "python", "-c", code, env=env).stdout
    return json.loads(out.strip().splitlines()[-1])


# Setup: secrets, certificates, the stack.

def write_state():
    STATE.mkdir(exist_ok=True)
    (STATE / "dynamic").mkdir(exist_ok=True)
    CERTS.mkdir(exist_ok=True)
    if not ENV.exists():
        # Kept between runs: the database volume keeps the passwords it was created with.
        def token():
            return secrets.token_urlsafe(32)

        lines = {
            "FLOPPY_DOMAIN": DOMAIN,
            "FLOPPY_PROXY_NETWORK": NETWORK,
            "FLOPPY_INTERNAL_SUBNET": "10.250.51.0/24",
            "FLOPPY_TRUSTED_PROXIES": SUBNET,
            "FLOPPY_META_CERT_RESOLVER": "",
            "FLOPPY_SECRET_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "FLOPPY_DB_OWNER_PASSWORD": token(),
            "FLOPPY_DB_APP_PASSWORD": token(),
            "FLOPPY_META_TOKEN": token(),
            "FLOPPY_META_ROUTES_TOKEN": ROUTES_TOKEN,
            "FLOPPY_SMTP_HOST": "smtp.invalid",
            "FLOPPY_SMTP_SENDER": "Floppy <floppy@floppy.test>",
            "FLOPPY_BOOTSTRAP_ORGANIZATION_NAME": "Studio Check",
            "FLOPPY_BOOTSTRAP_ORGANIZATION_SLUG": "studio-check",
            "FLOPPY_BOOTSTRAP_ADMIN_EMAIL": "admin@floppy.test",
            "FLOPPY_BOOTSTRAP_S3_ENDPOINT": "http://garage:3900",
            "FLOPPY_BOOTSTRAP_S3_REGION": "garage",
            "CHECK_GARAGE_RPC_SECRET": secrets.token_hex(32),
        }
        fd = os.open(ENV, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("".join(f"{k}={v}\n" for k, v in lines.items()))
    dist = os.environ.get("FLOPPY_JUICEFS_DIST") or str(ROOT / "spike" / "juicefs-replica" / "dist")
    if not dist.startswith("service:") and not (Path(dist) / "SHA256SUMS").is_file():
        sys.exit(f"no patched juicefs in {dist}: run spike/juicefs-replica/build.sh, set FLOPPY_JUICEFS_DIST, "
                 "or FLOPPY_JUICEFS_DIST=service:juicefs to build it in Compose")
    os.environ["FLOPPY_JUICEFS_DIST"] = dist
    # A new throwaway CA and certificate at every run.
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Floppy check CA",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", str(CERTS / "ca.key"), "-out", str(CERTS / "ca.crt"))
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={DOMAIN}",
        "-keyout", str(CERTS / "meta.key"), "-out", str(CERTS / "meta.csr"))
    # Python's ssl wants the leaf's authority key identifier and basic constraints.
    (CERTS / "meta.ext").write_text(f"subjectAltName=DNS:{DOMAIN},DNS:*.meta.{DOMAIN}\n"
                                    "extendedKeyUsage=serverAuth,clientAuth\nbasicConstraints=CA:FALSE\n"
                                    "authorityKeyIdentifier=keyid,issuer\nsubjectKeyIdentifier=hash\n")
    run("openssl", "x509", "-req", "-in", str(CERTS / "meta.csr"), "-CA", str(CERTS / "ca.crt"), "-CAkey",
        str(CERTS / "ca.key"), "-CAcreateserial", "-days", "2", "-extfile", str(CERTS / "meta.ext"),
        "-out", str(CERTS / "meta.crt"))
    for f in ("ca.crt", "meta.crt", "meta.key"):
        (CERTS / f).chmod(0o644)  # read by the containers' own users; a throwaway CA
    (STATE / "dynamic" / "tls.yml").write_text(
        "tls:\n  stores:\n    default:\n      defaultCertificate:\n"
        "        certFile: /etc/traefik/dynamic/meta.crt\n        keyFile: /etc/traefik/dynamic/meta.key\n")
    for f in ("meta.crt", "meta.key"):
        (STATE / "dynamic" / f).write_bytes((CERTS / f).read_bytes())


def setup():
    write_state()
    if run("docker", "network", "inspect", NETWORK, check=False).returncode != 0:
        run("docker", "network", "create", "--subnet", SUBNET, NETWORK)
    t = time.perf_counter()
    dc("up", "-d", "--build", "--wait", timeout=3600)
    record("c00 stack up and healthy (build included)", f"{time.perf_counter() - t:.0f} s", "-", True)
    # Garage: one node, a bucket and a key for the check's disk.
    def garage(*a, check=True):
        return dc("exec", "-T", "garage", "/garage", *a, check=check)

    for _ in range(30):
        if dc("exec", "-T", "garage", "/garage", "status", check=False).returncode == 0:
            break
        time.sleep(1)
    if "NO ROLE ASSIGNED" in garage("status").stdout:
        node = re.search(r"^([0-9a-f]+)\s.*NO ROLE ASSIGNED", garage("status").stdout, re.M).group(1)
        garage("layout", "assign", "-z", "local", "-c", "1G", node)
        garage("layout", "apply", "--version", "1")
    if garage("bucket", "info", "check-disk", check=False).returncode != 0:
        garage("bucket", "create", "check-disk")
    out = garage("key", "create", f"check-{int(time.time())}").stdout
    key = (re.search(r"Key ID:\s*(\S+)", out).group(1), re.search(r"Secret key:\s*(\S+)", out).group(1))
    garage("bucket", "allow", "--read", "--write", "--owner", "check-disk", "--key", key[0])
    return key


# HTTP and RESP over TLS to Traefik's published port, with the name in the handshake.

def tls_socket(host, port=HTTPS, timeout=10):
    ctx = ssl.create_default_context(cafile=str(CERTS / "ca.crt"))
    raw = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    return ctx.wrap_socket(raw, server_hostname=host)


def http_get(path, host=DOMAIN, tls=True):
    sock = tls_socket(host) if tls else socket.create_connection(("127.0.0.1", HTTP), timeout=10)
    try:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    finally:
        sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode(errors="replace").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    return status, headers, body


def resp(*parts):
    return b"*%d\r\n" % len(parts) + b"".join(b"$%d\r\n%s\r\n" % (len(p), p) for p in (x.encode() for x in parts))


def valkey_ping(host, password, user="default", idle=0.0):
    """AUTH and PING through Traefik; with `idle`, PING again after that many quiet seconds."""
    sock = tls_socket(host, timeout=max(10, idle + 10))
    try:
        sock.sendall(resp("AUTH", user, password) + resp("PING"))
        first = sock.recv(1024)
        if idle:
            time.sleep(idle)
            sock.sendall(resp("PING"))
            return first, sock.recv(1024)
        return first, b""
    finally:
        sock.close()


# The checks.

def web_checks():
    status, headers, body = http_get("/")
    csp = headers.get("content-security-policy", "")
    record("c01 web on 443 by name: index", f"{status}, {len(body)} bytes", "200", status == 200 and b'id="root"' in body)
    want = {"strict-transport-security": "max-age=63072000; includeSubDomains", "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer", "x-frame-options": "DENY"}
    missing = [k for k, v in want.items() if headers.get(k) != v]
    record("c01 web security headers", "all present" if not missing else f"missing {missing}", "all",
           not missing and "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "unsafe" not in csp)
    status, headers, _ = http_get("/people")
    record("c01 a deep link gets the app", f"{status}, cache {headers.get('cache-control')}", "200, no-cache",
           status == 200 and headers.get("cache-control") == "no-cache")
    status, headers, body = http_get("/api/v1/healthz")
    record("c02 API under /api/v1 on the same origin", f"{status} {body[:60]!r}", "200",
           status == 200 and headers.get("strict-transport-security", "").startswith("max-age=63072000"))
    status, headers, _ = http_get("/", tls=False)
    record("c03 plain HTTP redirects", f"{status} -> {headers.get('location')}", "301/308 https",
           status in (301, 308) and headers.get("location", "").startswith(f"https://{DOMAIN}/"))


CREATE = r"""
import asyncio, hashlib, json, os, time
from floppy_server.infra.meta import HttpMetaService
from floppy_server.infra.metadata import ACL_WRITE, ValkeyMetadataAdmin, ValkeyTarget
async def main():
    meta = HttpMetaService(os.environ["FLOPPY_META_URL"], os.environ["FLOPPY_META_TOKEN"])
    t = time.perf_counter()
    spec = await meta.create_instance(os.environ["CHECK_NAME"])
    took = time.perf_counter() - t
    target = ValkeyTarget(spec.admin_url, None, spec.admin_password)
    admin = ValkeyMetadataAdmin()
    ms = await admin.ping(target)
    digest = hashlib.sha256(os.environ["CHECK_DEVICE_PW"].encode()).digest()
    await admin.set_device_user(target, os.environ["CHECK_DEVICE_USER"], digest, "write")
    print(json.dumps({"url": spec.url, "admin_url": spec.admin_url, "password": spec.admin_password,
                      "took": took, "ping_ms": ms}))
asyncio.run(main())
"""

RELEASE = r"""
import asyncio, json, os
from floppy_server.infra.meta import HttpMetaService
meta = HttpMetaService(os.environ["FLOPPY_META_URL"], os.environ["FLOPPY_META_TOKEN"])
asyncio.run(meta.release_instance(os.environ["CHECK_NAME"], discard=True))
print(json.dumps({"ok": True}))
"""


def wait(predicate, limit):
    t = time.perf_counter()
    while time.perf_counter() - t < limit:
        try:
            if predicate():
                return time.perf_counter() - t
        except (OSError, ssl.SSLError):
            pass
        time.sleep(0.2)
    return -1.0


def replica_link():
    out = in_device(f"valkey-cli -p {REPLICA_PORT} -a \"$CHECK_DEVICE_PW\" --user \"$CHECK_DEVICE_USER\" --no-auth-warning "
                    "INFO replication", env=DEVICE_ENV, check=False).stdout
    m = re.search(r"master_link_status:(\w+)", out)
    return m.group(1) if m else "?"


def mode():
    out = in_device(f"grep '^juicefs_meta_replica_mode' {MNT[0]}/.stats", check=False).stdout.split()
    return out[-1] if out else None


DEVICE_ENV: dict[str, str] = {}
MNT = ["/mnt/disk"]


def meta_checks(key):
    name = f"check-{int(time.time())}"
    MNT[0] = mnt = f"/mnt/{name}"
    user, device_pw = "floppy_d_" + secrets.token_hex(16), secrets.token_urlsafe(24)
    DEVICE_ENV.update({"CHECK_DEVICE_USER": user, "CHECK_DEVICE_PW": device_pw})
    SECRETS.append(device_pw)
    spec = in_server(CREATE, env={"CHECK_NAME": name, "CHECK_DEVICE_USER": user, "CHECK_DEVICE_PW": device_pw})
    SECRETS.append(spec["password"])
    host = re.match(r"rediss://([^:/]+):443$", spec["url"]).group(1)
    label = host.split(".")[0]
    record("c04 the control plane creates a disk's instance on the meta service",
           f"{spec['took']:.2f} s, admin PING {spec['ping_ms']:.2f} ms on {spec['admin_url'].split('@')[-1]}", "< 10 s",
           spec["took"] < 10 and re.fullmatch(r"[a-z2-7]{16}", label) is not None and name not in spec["url"])
    try:
        routed = wait(lambda: valkey_ping(host, spec["password"])[0] == b"+OK\r\n+PONG\r\n", 30)
        record("c05 Traefik routes the new instance by name on 443 (HTTP provider)",
               f"after {routed:.1f} s" if routed >= 0 else "never", "< 10 s", 0 <= routed < 10)
        first, _ = valkey_ping(host, device_pw, user=user)
        record("c05 the device's user authenticates through Traefik", repr(first[:20]), "+OK +PONG",
               first == b"+OK\r\n+PONG\r\n")
        try:
            wrong = valkey_ping(f"nothing.meta.{DOMAIN}", spec["password"])[0]
        except (OSError, ssl.SSLError) as error:
            wrong = repr(error).encode()
        record("c05 a name with no router gets no Valkey", wrong[:40].decode(errors="replace"), "no PONG", b"PONG" not in wrong)
        try:
            web_as_valkey = valkey_ping(DOMAIN, spec["password"])[0]
        except (OSError, ssl.SSLError) as error:
            web_as_valkey = repr(error).encode()
        record("c05 the web's name stays HTTP", web_as_valkey[:30].decode(errors="replace"), "HTTP/1.1 400",
               web_as_valkey.startswith(b"HTTP/1.1 400"))

        in_device(f"grep -q ' {host}$' /etc/hosts || echo '{TRAEFIK_IP} {host}' >> /etc/hosts")
        # The replica, as the VM's floppy-replica@ runs it: by name over TLS, as the device's user.
        in_device(f"valkey-server --port {REPLICA_PORT} --daemonize yes --logfile /tmp/replica.log --save '' "
                  f"--appendonly no --replicaof {host} 443 --tls-replication yes --tls-cert-file /certs/meta.crt "
                  "--tls-key-file /certs/meta.key --tls-ca-cert-file /certs/ca.crt --masteruser \"$CHECK_DEVICE_USER\" "
                  "--masterauth \"$CHECK_DEVICE_PW\" --user \"$CHECK_DEVICE_USER\" on \">$CHECK_DEVICE_PW\" '~*' '&*' '+@all' "
                  "--replica-read-only yes", env=DEVICE_ENV)
        up = wait(lambda: replica_link() == "up", 30)
        record("c06 a Valkey replica links up through 443 by name", f"in {up:.1f} s" if up >= 0 else "down", "< 10 s",
               0 <= up < 10)

        s3 = {"ACCESS_KEY": key[0], "SECRET_KEY": key[1], "AWS_ACCESS_KEY_ID": key[0], "AWS_SECRET_ACCESS_KEY": key[1],
              "AWS_REGION": "garage"}  # server/docker/garage.toml's region
        fmt = in_device(f"juicefs format \"rediss://:$CHECK_ADMIN_PW@{host}:443/1?tls-ca-cert-file=/certs/ca.crt\" "
                        f"{name} --storage s3 --bucket http://garage:3900/check-disk --block-size 1024 --trash-days 0 "
                        "--no-update >/tmp/format.log 2>&1; rc=$?; tail -3 /tmp/format.log; exit $rc", env={**s3, "CHECK_ADMIN_PW": spec["password"]}, check=False)
        record("c07 the patched JuiceFS formats through 443", "ok" if fmt.returncode == 0 else "failed", "ok",
               fmt.returncode == 0, "" if fmt.returncode == 0 else fmt.stdout[-300:])
        mount_url = (f"rediss://$CHECK_DEVICE_USER:$CHECK_DEVICE_PW@{host}:443/1?tls-ca-cert-file=/certs/ca.crt"
                     f"&read-replica=redis://$CHECK_DEVICE_USER:$CHECK_DEVICE_PW@127.0.0.1:{REPLICA_PORT}/1&max-lag=2s")
        mounted = in_device(f"mkdir -p {mnt} && juicefs mount \"{mount_url}\" {mnt} --cache-dir /tmp/cache "
                            "--enable-xattr --no-usage-report -d --log /tmp/jfs.log >/tmp/mount.log 2>&1; rc=$?; "
                            "tail -3 /tmp/mount.log; exit $rc",
                            env={**DEVICE_ENV, **s3}, check=False)
        in_mode = wait(lambda: mode() == "1", 30)
        if in_mode < 0:
            record("c07 mount", "failed", "-", False, mounted.stdout[-400:])
        w = in_device(f"set -e; mountpoint -q {mnt}; cd {mnt}; head -c 3000000 /dev/urandom > blob; mkdir -p d; "
                      "for i in $(seq 1 20); do echo $i > d/f$i; done; mv d/f1 d/g1; rm d/f2; ls d | wc -l", check=False)
        record("c07 mount through 443 with the replica: write, rename, delete, read",
               f"mode 1 in {in_mode:.1f} s, {w.stdout.strip()} entries", "replica mode, 19",
               in_mode >= 0 and w.stdout.strip() == "19")

        # Quiet connections are not cut: Traefik's readTimeout is 0 on 443.
        first, second = valkey_ping(host, spec["password"], idle=75)
        record("c08 an idle connection through 443 survives 75 s", repr(second), "+PONG", second == b"+PONG\r\n")
        record("c08 the replica's link after the same 75 s", replica_link(), "up", replica_link() == "up")

        rtt = {}
        port = re.search(r":(\d+)$", spec["admin_url"]).group(1)
        for via, args in (("direct", f"-h floppy-meta -p {port}"),
                          ("Traefik+TLS", f"--tls --cacert /certs/ca.crt --sni {host} -h {host} -p 443")):
            out = in_device(f"t=$(date +%s%N); valkey-cli {args} --user \"$CHECK_DEVICE_USER\" -a \"$CHECK_DEVICE_PW\" "
                            "--no-auth-warning -r 2000 PING >/dev/null; echo $(( ($(date +%s%N) - t) / 2000 ))",
                            env=DEVICE_ENV).stdout.strip()
            rtt[via] = int(out) / 1e6
        added = rtt["Traefik+TLS"] - rtt["direct"]
        record("c09 one round trip (2,000 PINGs on one connection)",
               f"direct {rtt['direct']:.3f} ms, Traefik+TLS {rtt['Traefik+TLS']:.3f} ms", "adds < 1 ms", added < 1,
               f"(+{added:.3f} ms)")

        # The meta service restarted: its instances come back with their data and the device's user.
        t = time.perf_counter()
        dc("restart", "meta")
        back = wait(lambda: valkey_ping(host, device_pw, user=user)[0] == b"+OK\r\n+PONG\r\n", 60)
        record("c10 meta restarted: the instance answers the device's user through 443 again",
               f"after {time.perf_counter() - t:.1f} s" if back >= 0 else "never", "< 30 s", 0 <= back < 30)
        relinked = wait(lambda: replica_link() == "up", 60)
        remode = wait(lambda: mode() == "1", 60)
        files = in_device(f"mountpoint -q {mnt} && ls {mnt}/d | wc -l && md5sum {mnt}/blob | cut -c1-8", check=False).stdout.split()
        record("c10 replica and mount back, data intact", f"link in {relinked:.1f} s, mode 1 in {remode:.1f} s, {files}",
               "up, 1, 19 files", relinked >= 0 and remode >= 0 and files[:1] == ["19"])
    finally:
        in_device(f"mountpoint -q {mnt} && umount {mnt}; valkey-cli -p 6390 -a \"$CHECK_DEVICE_PW\" "
                  "--user \"$CHECK_DEVICE_USER\" --no-auth-warning shutdown nosave; true", env=DEVICE_ENV, check=False)
        in_server(RELEASE, env={"CHECK_NAME": name})


def browser_check():
    shot = STATE / "web.png"
    out = run("uv", "run", "--quiet", "--with", "playwright==1.55.0", "python", str(HERE / "browser.py"),
              f"https://{DOMAIN}:{HTTPS}/", str(shot), check=False, timeout=300)
    try:
        result = json.loads(out.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        record("c11 the web in Chrome with its CSP", "did not run", "-", False, (out.stdout + out.stderr)[-300:])
        return
    record("c11 the web in Chrome with its CSP: no violation, the app renders",
           f"{len(result['violations'])} violations, {result['rendered']} nodes, screenshot {shot.name}",
           "0, > 0", not result["violations"] and result["rendered"] > 0, "; ".join(result["violations"])[:300])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="leave the stack running")
    a = ap.parse_args()
    try:
        key = setup()
        web_checks()
        meta_checks(key)
        browser_check()
    except Exception as e:  # a crashed check is a failed check, reported with its cause
        record("crash", "crashed", "-", False, str(e)[:600])
    finally:
        if not a.keep:
            dc("down", check=False)
            run("docker", "network", "rm", NETWORK, check=False)
    failed = [r for r in RESULTS if not r[3]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
