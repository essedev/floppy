#!/usr/bin/env python3
"""One TLS port for every disk's Valkey, routed by SNI (DECISIONS #25).

Two disks' Valkey instances (valkey-m1, valkey-m2) sit behind Traefik on one
port; Traefik terminates TLS with a wildcard certificate for *.meta.floppy.test
and routes by the name in the handshake. One client (drive-t, the VM stand-in)
resolves the names to Traefik, mounts each disk with the patched JuiceFS over
rediss:// and holds a Valkey replica of each that replicates through the same
port with tls-replication. Each check prints its measure, its threshold and
PASS or FAIL.

Usage: spike/metatls.py [test ...]   tests m01 ... m06 (default: all)

Needs setup.sh run once (Garage, spike/.env), perdisk.py run once (its buckets)
and the patched juicefs in spike/juicefs-replica/dist; certificates come from
meta-tls.sh, run here.

TLS ends at Traefik and the hop to Valkey is plain on the private network: the
wildcard certificate lives in one place, renewed by Traefik, and the instances
the meta service starts need none. Valkey as a replica sends SNI when replicaof
names a host (connTLSConnect in src/tls.c), not an address.

Measured on 17/10/2026, all 14 checks passing: replicas through Traefik link up
in 0.1 s; given Traefik's address instead of the name, Traefik answers "HTTP/1.1
400" and the link stays down; mounts reach mode 1 in 0.2 s; with Traefik stopped
the mount goes offline after the 10 s master timeout (no refusal from a vanished
address), reads go on, writes fail with "Read-only file system", and it is back
in 1.5 s; one round trip 0.034 ms direct, 0.113 ms through Traefik and TLS; 100
small files 0.22 s direct and 0.26 s through Traefik, the 300-file cold listing
from the Mac 0.37 and 0.35 s.
"""

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import perdisk as pd

SPIKE = Path(__file__).resolve().parent
MNT = Path.home() / "FloppySpikeT"
SMB_PORT = 4514
STAMP = int(time.time())
DISKS = {"Progetti": 1, "Archivio": 2}
CA = "/certs/ca.crt"
SERVICES = ["drive-t", "valkey-t1", "valkey-t2", "traefik", "valkey-m1", "valkey-m2"]
record, timed, mac_try, RESULTS = pd.record, pd.timed, pd.mac_try, pd.RESULTS


def compose(*args, env=None, check=True, timeout=600):
    return pd.run("docker", "compose", "--profile", "metatls", *args, env=env, check=check, timeout=timeout)


def in_drive(script, env=None, check=False, timeout=600):
    flags = [x for name in (env or {}) for x in ("-e", name)]
    return compose("exec", "-T", *flags, "drive-t", "bash", "-c", script, env=env, check=check, timeout=timeout)


def admin(n, *args):
    return compose("exec", "-T", "-e", "REDISCLI_AUTH", f"valkey-m{n}", "valkey-cli", *args,
                   env={"REDISCLI_AUTH": pd.VALKEY_PW}).stdout.strip()


def cli(n, *args, check=False):
    """valkey-cli in disk n's replica container (drive-t's namespace), password from the environment."""
    return compose("exec", "-T", "-e", "REDISCLI_AUTH", f"valkey-t{n}", "valkey-cli", *args,
                   env={"REDISCLI_AUTH": pd.VALKEY_PW}, check=check, timeout=120)


def tls_args(n, sni=None):
    host = f"disk{n}.meta.floppy.test"
    return ["--tls", "--cacert", CA, "--sni", sni or host, "-h", host, "-p", "6443"]


def url(n, via="tls", user=pd.DEVICE, replica=True):
    """Disk n's metadata URL: through Traefik ("tls") or straight to the instance ("direct")."""
    cred = f"{user}:{pd.DEVICE_PW}" if user else f":{pd.VALKEY_PW}"
    if via == "tls":
        u = f"rediss://{cred}@disk{n}.meta.floppy.test:6443/1?tls-ca-cert-file={CA}"
    else:
        u = f"redis://{cred}@valkey-m{n}:6379/1?"
    if replica:
        u += f"&read-replica=redis://:{pd.VALKEY_PW}@127.0.0.1:638{n}/1&max-lag=2s"
    return u.replace("?&", "?").rstrip("?")


def mount(name, via="tls"):
    n = DISKS[name]
    in_drive(f"/perdisk-entrypoint.sh folder add {name}", check=True)
    in_drive(f"juicefs mount '{url(n, via)}' /mnt/drive/{name} --cache-dir /cache/d{n} --enable-xattr "
             f"--no-usage-report -d --log /var/log/jfs-d{n}.log >/dev/null 2>&1", env=pd.s3_env(pd.DEVICE_KEY), check=True)
    return wait_mode(name, 1)


def unmount(name):
    in_drive(f"mountpoint -q /mnt/drive/{name} && umount /mnt/drive/{name}; true")


def mode(name):
    out = in_drive(f"grep '^juicefs_meta_replica_mode' /mnt/drive/{name}/.stats").stdout.split()
    return out[-1] if out else None


def wait_mode(name, want, limit=60):
    t = time.perf_counter()
    while time.perf_counter() - t < limit:
        if mode(name) == str(want):
            return time.perf_counter() - t
        time.sleep(0.2)
    raise RuntimeError(f"{name}: mode {want} not reached in {limit} s (now {mode(name)})")


def link(n):
    m = re.search(r"master_link_status:(\w+)", cli(n, "-p", f"638{n}", "INFO", "replication").stdout)
    return m.group(1) if m else "?"


def wait_link(n, limit=30):
    t = time.perf_counter()
    while time.perf_counter() - t < limit:
        if link(n) == "up":
            return time.perf_counter() - t
        time.sleep(0.2)
    return -1


def start_replicas(env=None):
    compose("up", "-d", "--force-recreate", "valkey-t1", "valkey-t2", env=env)


def mac_mount():
    pd.MNT, pd.SMB_PORT = MNT, SMB_PORT
    pd.mac_mount()


def mac_unmount():
    pd.MNT = MNT
    pd.mac_unmount()


def setup():
    mac_unmount()
    compose("rm", "-sfv", *SERVICES, check=False)
    pd.run(str(SPIKE / "meta-tls.sh"))
    compose("up", "-d", "garage", "valkey-m1", "valkey-m2", "traefik")
    compose("up", "-d", "--build", "drive-t")
    out =compose("exec", "-T", "garage", "/garage", "key", "create", f"perdisk-dev-{STAMP}").stdout
    pd.DEVICE_KEY = (re.search(r"Key ID:\s*(\S+)", out).group(1), re.search(r"Secret key:\s*(\S+)", out).group(1))
    for n in DISKS.values():
        compose("exec", "-T", "garage", "/garage", "bucket", "allow", "--read", "--write", pd.BUCKET.format(n),
                "--key", pd.DEVICE_KEY[0])
        admin(n, "FLUSHALL")
        admin(n, "ACL", "SETUSER", pd.DEVICE, "reset", "on", f">{pd.DEVICE_PW}", *pd.ACL_WRITE)
        # The control plane formats through the same port, as the admin.
        in_drive(f"juicefs format '{url(n, user=None, replica=False)}' tls{n}-{STAMP} --storage s3 "
                 f"--bucket http://garage:3900/{pd.BUCKET.format(n)} --block-size 1024 --trash-days 0 >/dev/null 2>&1",
                 env=pd.s3_env(pd.CTL_KEY), check=True)


def teardown():
    mac_unmount()
    compose("rm", "-sfv", *SERVICES, check=False)
    if pd.DEVICE_KEY:
        compose("exec", "-T", "garage", "/garage", "key", "delete", "--yes", pd.DEVICE_KEY[0], check=False)
    compose("rm", "-sf", "garage", check=False)


# The tests.

def m01():
    """Point 2: a Valkey replica replicates from each master through the one port."""
    start_replicas()
    for n in DISKS.values():
        s = wait_link(n)
        record(f"m01 replica of disk {n} through Traefik (TLS, SNI from the name)", f"link up in {s:.1f} s" if s >= 0
               else "link down", "up", s >= 0)
    # The name decides the route: a wrong name or a bare address reaches no instance.
    r = cli(1, *tls_args(1, sni="nothing.meta.floppy.test"), "PING")
    wrong = r.stdout + r.stderr
    record("m01 a name with no router is refused", wrong.strip().splitlines()[-1][:80] if wrong.strip() else "(closed)",
           "no PONG", "PONG" not in wrong)
    compose("up", "-d", "--force-recreate", "valkey-t1", env={"REPLICA_OF_1": "10.250.45.240 6443"})
    time.sleep(5)
    lk = link(1)
    logs = compose("logs", "--tail", "40", "valkey-t1").stdout
    why = next((x.split("* ")[-1] for x in logs.splitlines()[::-1] if "rror" in x or "TLS" in x), "")
    record("m01 the same replica given Traefik's address (no SNI) does not replicate", f"link {lk}", "down",
           lk == "down", why[-100:])
    start_replicas()
    for n in DISKS.values():
        wait_link(n)


def m02():
    """Point 1 and 3: the patched JuiceFS mounts each disk over rediss:// through Traefik, reads from its replica."""
    for name, n in DISKS.items():
        s = mount(name)
        r = in_drive(f"set -e; cd /mnt/drive/{name}; head -c 3000000 /dev/urandom > blob; mkdir -p d; "
                     "for i in $(seq 1 20); do echo $i > d/f$i; done; mv d/f1 d/g1; rm d/f2; "
                     "md5sum blob | cut -c1-8; ls d | wc -l")
        out = r.stdout.split()
        record(f"m02 {name}: mount through Traefik, write, rename, delete, read", f"mode 1 in {s:.1f} s, {out}",
               "mode 1, 19 entries", r.returncode == 0 and out[-1:] == ["19"])
    log = in_drive("grep -h -oE 'Read replica .* mode [a-z]+ -> replica' /var/log/jfs-d1.log | tail -1").stdout.strip()
    record("m02 the patch's probe reaches the master through Traefik", log[-60:] or "no log", "replica mode", "-> replica" in log)


def m03():
    """Point 3: offline when Traefik stops, back when it returns."""
    compose("stop", "traefik")
    try:
        s = wait_mode("Progetti", 2, limit=30)
        read = in_drive("timeout 20 cat /mnt/drive/Progetti/d/f3").stdout.strip()
        w = in_drive("timeout 20 sh -c 'echo x > /mnt/drive/Progetti/off' 2>&1; echo rc=$?").stdout.strip()
    finally:
        compose("start", "traefik")
    back = wait_mode("Progetti", 1, limit=60)
    record("m03 Traefik stopped: offline, reads go on, writes refused", f"offline in {s:.1f} s, read {read!r}, {w}",
           "offline, '3', Read-only", read == "3" and "Read-only" in w)
    record("m03 Traefik back: replica mode again", f"mode 1 after {back:.1f} s", "< 10 s", back < 10)


def m04():
    """Point 4: what Traefik and TLS add to one round trip."""
    res = {}
    for label, args in (("direct", ["-h", "valkey-m1", "-p", "6379"]), ("Traefik+TLS", tls_args(1))):
        r = compose("exec", "-T", "-e", "REDISCLI_AUTH", "valkey-t1", "sh", "-c",
                    f"t=$(date +%s%N); valkey-cli {' '.join(args)} -r 2000 PING >/dev/null; echo $(( ($(date +%s%N) - t) / 2000 ))",
                    env={"REDISCLI_AUTH": pd.VALKEY_PW}).stdout.strip()
        res[label] = int(r) / 1e6  # nanoseconds per PING, in ms
    added = res["Traefik+TLS"] - res["direct"]
    record("m04 one round trip (2,000 PINGs on one connection)", f"direct {res['direct']:.3f} ms, Traefik+TLS "
           f"{res['Traefik+TLS']:.3f} ms", "adds < 1 ms", added < 1, f"(+{added:.3f} ms)")


def m05():
    """Point 4: a metadata-heavy check through Traefik and direct: 100 creates in the VM, 300-file cold listing from the Mac."""
    in_drive("set -e; mkdir -p /mnt/drive/Progetti/media; head -c 65536 /dev/urandom > /tmp/blob; "
             "for i in $(seq -w 0 299); do cp /tmp/blob /mnt/drive/Progetti/media/clip_$i.mov; done", check=True)
    for via in ("tls", "direct"):
        mac_unmount()
        unmount("Progetti")
        mount("Progetti", via)
        r = in_drive("d=/mnt/drive/Progetti/small-$RANDOM; mkdir $d; t=$(date +%s%N); "
                     "for i in $(seq 1 100); do echo '{}' > $d/f$i.json; done; echo $(( ($(date +%s%N) - t) / 1000000 ))")
        mac_mount()
        s, n = timed(lambda: len([e.stat() for e in os.scandir(MNT / "Progetti" / "media")]))
        record(f"m05 {via}: 100 small files created in the VM", f"{int(r.stdout.strip()) / 1000:.2f} s", "-", True)
        record(f"m05 {via}: cold listing of 300 files from the Mac", f"{s:.2f} s ({n} files)", "< 0.5 s", s < 0.5 and n == 300)


TESTS = [m01, m02, m03, m04, m05]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tests", nargs="*")
    a = ap.parse_args()
    chosen = [t for t in TESTS if not a.tests or t.__name__ in a.tests]
    try:
        setup()
        if m01 not in chosen:
            start_replicas()
        for t in chosen:
            try:
                t()
            except Exception as e:  # a crashed test is a failed test, reported with its cause
                record(t.__name__, "crashed", "-", False, str(e)[:300])
    finally:
        teardown()
    failed = [r for r in RESULTS if not r[3]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
