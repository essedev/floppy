#!/usr/bin/env python3
"""Per-disk permissions: one Valkey instance and one bucket per disk.

One client (drive-p, the VM stand-in) sees three disks, each with its own Valkey
master behind the simulated link and its own bucket on Garage. The person's
device has one Valkey user on each instance it may see and one S3 key granted
per bucket: read-write on Progetti, read-only on Archivio, nothing on Riservato.
Each visible disk is a JuiceFS mount at /mnt/drive/<disk> with a local replica
of its instance; Samba exports /mnt/drive as the one Floppy share (DECISIONS #20)
and the Mac mounts it. Each check prints its measure, its threshold and PASS or
FAIL; t14 and t25 print the numbers to record.

Usage: spike/perdisk.py [test ...]   tests t14 ... t25 (default: all)
  ROOT_MODE=immutable spike/perdisk.py    the share's root with chattr +i (all 58 checks pass)
  SMB_FORCE_USER=drive spike/perdisk.py   Samba as an unprivileged user instead of root
  SQUASH=drive|root                       juicefs mount --all-squash to that account
  ROOT_MODE=tmpfs spike/perdisk.py        the share's root on a read-only tmpfs
  FILES=10000                             files per disk for t25

Needs setup.sh run once (Garage layout, spike/.env) and the patched juicefs in
spike/juicefs-replica/dist (build.sh): drive-p is built with it.

Measured on 17/10/2026 at 15 ms. The share's root: with Samba as root, 0555 alone
lets the Mac write there (t24); Samba as drive stops it but cannot touch files root
wrote before (t26), with or without --all-squash (Samba checks the mode bits itself,
and squashing leaves the stored owners as they are); ROOT_MODE=immutable with Samba
as root passes everything:
- idle, per disk: replica 12-15 MB RSS, JuiceFS mount 34-39 MB private memory
  (165-170 MB RSS, the binary's pages counted in both of its processes); smbd 39 MB;
- cold listing of 300 files in one disk from the Mac: 0.31-0.45 s;
- read-only disk: refused writes in 0.00-0.01 s from the shell ("Read-only file
  system"), Finder error -61 ("you don't have the necessary permission"), delete
  in 9.8 s; a tampered read-write mount fails at session creation in 10.2 s (NOPERM);
- VM start with three disks of 10,000 files: RDB 1.2-1.3 MB per disk, all in mode 1
  in 5.6 s, of which 5 s is the master's repl-diskless-sync-delay; 1.8 s with
  SYNC_DELAY=0.
"""

import argparse
import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
MNT = Path.home() / "FloppySpikeP"
SMB_PORT = 4510
LATENCY_MS = int(os.environ.get("LATENCY_MS", "15"))
STAMP = int(time.time())
RESULTS = []

# Display name (the folder) -> instance number. Instance N listens on toxiproxy:451N
# (master, through the link) and on 127.0.0.1:638N in drive-p (its replica).
DISKS = {"Progetti": 1, "Archivio": 2, "Riservato": 3}
BUCKET = "floppy-disk{}"
DEVICE = "dev-p"  # the device's Valkey user, the same name on every instance it may see


def env_val(name):
    for line in (SPIKE / ".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    sys.exit(f"perdisk: {name} missing in spike/.env, run setup.sh")


VALKEY_PW = env_val("VALKEY_PASSWORD")
SMB_PW = env_val("SMB_PASSWORD")
DEVICE_PW = VALKEY_PW + "-dev"
# The control plane's key: owner of every bucket, used to format.
CTL_KEY = (env_val("ACCESS_KEY"), env_val("SECRET_KEY"))
DEVICE_KEY = None  # (id, secret), created by setup for this run

# Minimal ACLs: what the master's commandstats showed for a full workload (mount, SMB
# browsing, writes, renames, xattrs, heartbeats, unmount), checked by an empty ACL LOG
# (t15). Order matters: -@dangerous drops KEYS, FLUSHDB, INFO, PSYNC and REPLCONF, and
# the needed ones come back after it. Replication needs PSYNC, REPLCONF and PING; the
# patched client's probe reads INFO replication (without it the mount never leaves
# master mode); JuiceFS sends CLUSTER INFO once to detect a cluster (a refusal works,
# but fills the ACL LOG). Writes add the commands, MULTI/EXEC/WATCH and the scripts:
# EVALSHA/EVAL for the optimistic commit and the lookups, SCRIPT LOAD at session start.
ACL_READ = ["resetkeys", "~*", "resetchannels", "-@all", "+@read", "-@dangerous",
            "+ping", "+select", "+info", "+cluster|info", "+psync", "+replconf"]
ACL_WRITE = ["resetkeys", "~*", "resetchannels", "-@all", "+@read", "+@write", "+@transaction", "-@dangerous",
             "+ping", "+select", "+info", "+cluster|info", "+psync", "+replconf", "+eval", "+evalsha", "+script|load"]


def run(*cmd, check=True, timeout=600, env=None, cwd=SPIKE):
    """Runs a command; returns the completed process."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, **(env or {})))
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:5])}...: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def compose(*args, env=None, **kw):
    return run("docker", "compose", "--profile", "perdisk", *args, env=env, **kw)


def exec_in(service, script, env=None, check=True, timeout=600):
    """A bash script inside a service. Variables in env reach it by name, never through argv."""
    flags = [x for name in (env or {}) for x in ("-e", name)]
    return compose("exec", "-T", *flags, service, "bash", "-c", script, env=env, check=check, timeout=timeout)


def admin(n, *args):
    """A command on disk n's master as its admin."""
    r = compose("exec", "-T", "-e", "REDISCLI_AUTH", f"valkey-disk{n}", "valkey-cli", *args,
                env={"REDISCLI_AUTH": VALKEY_PW})
    return r.stdout.strip()


def as_device(n, *args, via="valkey-p1"):
    """A command on disk n's master as the device's user, from inside the device (drive-p's namespace)."""
    r = compose("exec", "-T", "-e", "REDISCLI_AUTH", via, "valkey-cli", "-h", "toxiproxy", "-p", f"451{n}",
                "--user", DEVICE, *args, env={"REDISCLI_AUTH": DEVICE_PW}, check=False, timeout=60)
    return (r.stdout + r.stderr).strip()


def replica_cli(n, *args):
    r = compose("exec", "-T", "-e", "REDISCLI_AUTH", f"valkey-p{n}", "valkey-cli", "-p", f"638{n}", *args,
                env={"REDISCLI_AUTH": VALKEY_PW}, check=False, timeout=60)
    return r.stdout.strip()


def toxi(*args, check=True):
    return compose("exec", "-T", "toxiproxy", "/toxiproxy-cli", *args, check=check).stdout


def garage(*args, check=True):
    return compose("exec", "-T", "garage", "/garage", *args, check=check).stdout


def record(test, measure, threshold, ok, note=""):
    RESULTS.append((test, measure, threshold, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {test}: {measure} (threshold {threshold}) {note}", flush=True)


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return time.perf_counter() - t, out


# Storage: one bucket per disk, the control plane's key owns them all, the device's
# key is granted per bucket.

def set_device_grants(grants):
    """grants: {n: "rw" | "r" | None} on bucket n for the device's key."""
    for n, g in grants.items():
        garage("bucket", "deny", "--read", "--write", "--owner", BUCKET.format(n), "--key", DEVICE_KEY[0])
        if g:
            garage("bucket", "allow", "--read", *(["--write"] if g == "rw" else []), BUCKET.format(n),
                   "--key", DEVICE_KEY[0])


def setup_storage():
    global DEVICE_KEY
    drop_device_keys()
    for n in DISKS.values():
        if garage("bucket", "info", BUCKET.format(n), check=False).find("Bucket:") < 0:
            garage("bucket", "create", BUCKET.format(n))
        garage("bucket", "allow", "--read", "--write", "--owner", BUCKET.format(n), "--key", CTL_KEY[0])
    out = garage("key", "create", f"perdisk-dev-{STAMP}")
    DEVICE_KEY = (re.search(r"Key ID:\s*(\S+)", out).group(1), re.search(r"Secret key:\s*(\S+)", out).group(1))


def drop_device_keys():
    """Deletes this suite's device keys, this run's and any left by an interrupted one."""
    for line in garage("key", "list", check=False).splitlines():
        cols = line.split()
        if len(cols) >= 3 and cols[2].startswith("perdisk-dev-"):
            garage("key", "delete", "--yes", cols[0], check=False)


def s3_env(key):
    return {"AWS_ACCESS_KEY_ID": key[0], "AWS_SECRET_ACCESS_KEY": key[1], "AWS_REGION": "us-east-1"}


def s3(key, method, path, data=None):
    """An S3 request to Garage (direct port) signed with key; returns the HTTP status. The
    secret reaches curl on stdin, never argv."""
    cfg = f'user = "{key[0]}:{key[1]}"\n'
    args = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-K", "-", "-X", method,
            "--aws-sigv4", "aws:amz:us-east-1:s3", f"http://127.0.0.1:4500/{path}"]
    if data is not None:
        args += ["-H", "Content-Type: application/octet-stream", "--data-binary", data]
    r = subprocess.run(args, input=cfg, capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def object_count(n):
    out = garage("bucket", "info", BUCKET.format(n))
    m = re.search(r"Objects:\s*(\d+)", out)
    return int(m.group(1)) if m else -1


# Metadata: one Valkey instance per disk.

def meta_url(n, user=DEVICE, replica=True):
    cred = f"{user}:{DEVICE_PW}" if user else f":{VALKEY_PW}"
    url = f"redis://{cred}@toxiproxy:451{n}/1"
    if replica:
        url += f"?read-replica=redis://:{VALKEY_PW}@127.0.0.1:638{n}/1&max-lag=2s"
    return url


def set_user(n, perm):
    """The device's user on disk n's instance: "rw", "r", or None (no user at all)."""
    if perm is None:
        admin(n, "ACL", "DELUSER", DEVICE)
    else:
        # Valkey checks the ACL on every command: a changed user changes its open connections
        # too, and deleting it closes them.
        admin(n, "ACL", "SETUSER", DEVICE, "reset", "on", f">{DEVICE_PW}", *(ACL_WRITE if perm == "rw" else ACL_READ))


# SQUASH maps every caller of a disk's mount to one account (`juicefs mount --all-squash`),
# which also turns off the kernel's permission check: "drive" to Samba's unprivileged
# user, "root" to uid 0 (JuiceFS then checks nothing), "" for no mapping.
SQUASH = os.environ.get("SQUASH", "")


def squash_flag():
    if not SQUASH:
        return ""
    if SQUASH == "root":
        return " --all-squash 0:0"
    ids = in_drive(f"echo $(id -u {SQUASH}):$(id -g {SQUASH})", check=True).stdout.strip()
    return f" --all-squash {ids}"


def mount_flags(n, read_only):
    return (f"--cache-dir /cache/d{n} --cache-size 10240 --buffer-size 256 --max-readahead 4 --prefetch 8 "
            f"--enable-xattr --cache-large-write --no-usage-report -d --log /var/log/jfs-d{n}.log"
            + (" --read-only" if read_only else "") + squash_flag())


def mount_disk(name, perm, wait=True, folder=True):
    """Mounts disk `name` in the share as the device: with --read-only for "r"."""
    n = DISKS[name]
    if folder:
        exec_in("drive-p", f"/perdisk-entrypoint.sh folder add '{name}'")
    exec_in("drive-p", f"juicefs mount '{meta_url(n)}' '/mnt/drive/{name}' {mount_flags(n, perm == 'r')} >/dev/null 2>&1",
            env=s3_env(DEVICE_KEY))
    if wait:
        return wait_mode(name, 1)


def unmount_disk(name, remove_folder=False):
    exec_in("drive-p", f"mountpoint -q '/mnt/drive/{name}' && umount '/mnt/drive/{name}'; true")
    for _ in range(50):
        if exec_in("drive-p", f"mountpoint -q '/mnt/drive/{name}'", check=False).returncode != 0:
            break
        time.sleep(0.2)
    if remove_folder:
        exec_in("drive-p", f"/perdisk-entrypoint.sh folder remove '{name}'")


def mode(name):
    r = exec_in("drive-p", f"grep '^juicefs_meta_replica_mode' '/mnt/drive/{name}/.stats'", check=False)
    return r.stdout.split()[-1] if r.stdout.split() else None


def wait_mode(name, want, limit=60):
    t = time.perf_counter()
    while time.perf_counter() - t < limit:
        if mode(name) == str(want):
            return time.perf_counter() - t
        time.sleep(0.25)
    raise RuntimeError(f"{name}: mode {want} not reached in {limit} s (now {mode(name)})")


def start_replicas(ns):
    compose("up", "-d", "--force-recreate", *[f"valkey-p{n}" for n in ns],
            env={"PERDISK_USER": DEVICE, "PERDISK_PASSWORD": DEVICE_PW})


def replica_synced(n):
    info = replica_cli(n, "INFO", "replication")
    return "master_link_status:up" in info and "master_sync_in_progress:0" in info


def admin_mount(n, path, script, env_key=CTL_KEY):
    """Runs script with disk n mounted read-write by the control plane at path (outside the share)."""
    exec_in("drive-p", f"""set -e; mkdir -p {path}
juicefs mount '{meta_url(n, user=None, replica=False)}' {path} --cache-dir /cache/admin{n} --no-usage-report \
  -d --log /var/log/jfs-admin{n}.log >/dev/null 2>&1
cd {path}; {script}; cd /; umount {path}""", env=s3_env(env_key), timeout=3600)


def format_disk(name):
    n = DISKS[name]
    admin(n, "FLUSHALL")
    # No ACCESS_KEY in the environment: the format holds no key and every mount brings its own.
    exec_in("drive-p", f"juicefs format '{meta_url(n, user=None, replica=False)}' {name.lower()}-{STAMP} "
            f"--storage s3 --bucket http://toxiproxy:4501/{BUCKET.format(n)} --block-size 1024 --compress none "
            f"--trash-days 7 >/dev/null 2>&1", env=s3_env(CTL_KEY))


def mac_mount():
    MNT.mkdir(exist_ok=True)
    for _ in range(40):
        if subprocess.run(["/sbin/mount_smbfs", f"//drive:{SMB_PW}@localhost:{SMB_PORT}/Floppy", str(MNT)],
                          capture_output=True).returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("cannot mount the share")


def mac_unmount():
    for _ in range(5):
        if f" on {MNT} " not in subprocess.run(["/sbin/mount"], capture_output=True, text=True).stdout:
            return
        subprocess.run(["/sbin/umount", "-f", str(MNT)], capture_output=True)
        time.sleep(1)


def proxy(name, listen, upstream, latency):
    toxi("delete", name, check=False)
    toxi("create", "-l", listen, "-u", upstream, name)
    toxi("toxic", "add", "-t", "latency", "-a", f"latency={latency}", name)


def set_latency(ms):
    for n in DISKS.values():
        toxi("toxic", "update", "-n", "latency_downstream", "-a", f"latency={ms}", f"disk{n}")


SERVICES = ["drive-p", "valkey-p1", "valkey-p2", "valkey-p3", "valkey-disk1", "valkey-disk2", "valkey-disk3"]


def setup():
    """Fresh instances and volumes; permissions rw on Progetti, r on Archivio, rw on Riservato
    (hidden later by t23); all three mounted and the share mounted on the Mac."""
    mac_unmount()
    compose("rm", "-sfv", *SERVICES, check=False)
    compose("up", "-d", "garage", "toxiproxy", "valkey-disk1", "valkey-disk2", "valkey-disk3")
    compose("up", "-d", "--build", "drive-p")
    if "s3" not in toxi("list"):
        proxy("s3", "0.0.0.0:4501", "garage:3900", 25)
    for n in DISKS.values():
        proxy(f"disk{n}", f"0.0.0.0:451{n}", f"valkey-disk{n}:6379", LATENCY_MS)
    setup_storage()
    for name in DISKS:
        format_disk(name)
    # Content made by the control plane's side (another person), before the device mounts.
    admin_mount(2, "/seed/2", "mkdir -p clips && for i in $(seq 1 20); do head -c 65536 /dev/urandom > clips/c$i.mov; done;"
                " head -c 20000000 /dev/urandom > clips/big.mov; echo archived > note.txt")
    admin_mount(3, "/seed/3", "echo secret > secret.txt")
    perms = {"Progetti": "rw", "Archivio": "r", "Riservato": "rw"}
    set_device_grants({DISKS[k]: v for k, v in perms.items()})
    for name, p in perms.items():
        set_user(DISKS[name], p)
    start_replicas(DISKS.values())
    for name, p in perms.items():
        mount_disk(name, p)
    mac_mount()


    STATE["objects2"] = object_count(2)
    for n in DISKS.values():
        admin(n, "ACL", "LOG", "RESET")
        admin(n, "CONFIG", "RESETSTAT")


STATE = {}


def teardown():
    mac_unmount()
    compose("rm", "-sfv", *SERVICES, check=False)
    for name in DISKS:
        toxi("delete", f"disk{DISKS[name]}", check=False)
    drop_device_keys()


def in_drive(script, check=False, timeout=600):
    return exec_in("drive-p", script, check=check, timeout=timeout)


def mac_try(*cmd):
    """A command on the Mac: (ok, seconds, error text)."""
    t = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return r.returncode == 0, time.perf_counter() - t, (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]


def commandstats(n):
    """{command: calls} on disk n's master since the last CONFIG RESETSTAT."""
    out = {}
    for m in re.finditer(r"cmdstat_([^:]+):calls=(\d+),.*?rejected_calls=(\d+)", admin(n, "INFO", "commandstats")):
        out[m.group(1)] = int(m.group(2))
    return out


def acl_log(n):
    """The commands refused by ACL on disk n's master: [(user, command)]."""
    lines = admin(n, "ACL", "LOG").splitlines()
    out = []
    for i, line in enumerate(lines):
        if line == "object" and i + 3 < len(lines):
            out.append((lines[i + 3], lines[i + 1]))
    return out


# The tests. Thresholds are the acceptance criteria agreed before measuring.

def t14():
    """Point 1: three disks, three masters, three replicas, three mounts; memory; cold listing."""
    time.sleep(10)  # idle
    for n in DISKS.values():
        rss = re.search(r"used_memory_rss:(\d+)", replica_cli(n, "INFO", "memory"))
        mb = int(rss.group(1)) / 2**20 if rss else -1
        record(f"t14a replica of disk {n}, idle RSS", f"{mb:.1f} MB", "< 30 MB", 0 < mb < 30)
    # Each mount is two processes, a supervisor and the mount itself, sharing the binary's
    # pages: RSS counts those twice, private memory (smaps_rollup) is what each one adds.
    ps = in_drive("for p in $(pgrep -x juicefs); do printf '%s %s ' \"$(awk '/^Rss/ {print $2}' /proc/$p/smaps_rollup)\" "
                  "\"$(awk '/^Private/ {s += $2} END {print s}' /proc/$p/smaps_rollup)\"; "
                  "tr '\\0' ' ' < /proc/$p/cmdline | grep -o '/mnt/drive/[^ ]*'; done").stdout.splitlines()
    for name in DISKS:
        procs = [(int(line.split()[0]), int(line.split()[1])) for line in ps if line.endswith(f"/mnt/drive/{name}")]
        rss, priv = sum(p[0] for p in procs) / 1024, sum(p[1] for p in procs) / 1024
        record(f"t14b JuiceFS mount of {name}, idle private memory", f"{priv:.0f} MB in {len(procs)} processes",
               "< 100 MB", 0 < priv < 100, f"(RSS {rss:.0f} MB with the shared binary)")
    smbd = sum(int(x) for x in in_drive("ps -C smbd -o rss=").stdout.split()) / 1024
    record("t14c smbd, idle RSS (all processes)", f"{smbd:.0f} MB", "-", True)

    in_drive("set -e; mkdir -p /mnt/drive/Progetti/media; cd /mnt/drive/Progetti/media; "
             "head -c 65536 /dev/urandom > /tmp/blob; for i in $(seq -w 0 299); do cp /tmp/blob clip_$i.mov; done", check=True)
    # Cold: the Mac's SMB client and the disk's JuiceFS mount both start empty.
    mac_unmount()
    unmount_disk("Progetti")
    mount_disk("Progetti", "rw")
    mac_mount()
    s, n = timed(lambda: len([e.stat() for e in os.scandir(MNT / "Progetti" / "media")]))
    record("t14d cold listing of 300 files in one disk from the Mac", f"{s:.2f} s ({n} files)", "< 0.5 s", s < 0.5 and n == 300)
    s, _ = timed(lambda: [e.stat() for e in os.scandir(MNT / "Progetti" / "media")])
    record("t14e warm listing of the same folder", f"{s:.2f} s", "-", True)
    ok, s, err = mac_try("cp", "/etc/services", str(MNT / "Progetti" / "services"))
    same = ok and (MNT / "Progetti" / "services").read_bytes() == Path("/etc/services").read_bytes()
    record("t14f read-write disk: copy in from the Mac and read back", "ok" if same else err[0], "ok", same, f"({s:.2f} s)")


def t15():
    """Points 2 and 6: the read-only user sends only reads and replication to its master."""
    in_drive("ls -laR /mnt/drive/Archivio >/dev/null; cat /mnt/drive/Archivio/note.txt >/dev/null; "
             "getfattr -d /mnt/drive/Archivio/note.txt >/dev/null 2>&1; true")
    subprocess.run(["ls", "-laR", str(MNT / "Archivio")], capture_output=True)
    stats = commandstats(2)
    writes = {c: v for c, v in stats.items() if c in {"hincrby", "multi", "exec", "watch", "evalsha", "eval", "set",
                                                      "hset", "incr", "incrby", "zadd", "del", "rpush", "script|load"}}
    record("t15a read-only disk: write commands on its master from the device", f"{writes or 'none'}", "none", not writes,
           f"(commands seen: {', '.join(sorted(c for c in stats if not c.startswith(('acl', 'config'))))})")
    refused = {n: acl_log(n) for n in DISKS.values()}
    bad = {n: v for n, v in refused.items() if v}
    record("t15b minimal ACLs: commands refused by Valkey during setup and use", f"{bad or 'none'}", "none", not bad)
    modes = {name: mode(name) for name in DISKS}
    record("t15c every disk reads from its replica (mode 1)", str(modes), "all 1", set(modes.values()) == {"1"})


def t16():
    """Point 2: browsing and reading a read-only disk from the Mac."""
    names = sorted(os.listdir(MNT / "Archivio" / "clips"))
    want = in_drive("md5sum < /mnt/drive/Archivio/clips/big.mov").stdout.split()[0]
    s, got = timed(lambda: subprocess.run(["/sbin/md5", "-q", str(MNT / "Archivio" / "clips" / "big.mov")],
                                          capture_output=True, text=True).stdout.strip())
    ok = len(names) == 21 and got == want
    record("t16 read-only disk: browse and read from the Mac", f"{len(names)} files, 20 MB read {'ok' if got == want else 'MISMATCH'}",
           "21 files, same bytes", ok, f"({s:.2f} s)")


def t17():
    """Point 2: writes to a read-only disk from the Mac fail at once, reads keep working."""
    a = MNT / "Archivio"
    tries = [
        ("copy a file in", ["cp", "/etc/hosts", str(a / "hosts")]),
        ("create a folder", ["mkdir", str(a / "new")]),
        ("rename", ["mv", str(a / "note.txt"), str(a / "renamed.txt")]),
        ("delete", ["rm", str(a / "note.txt")]),
        ("overwrite", ["sh", "-c", f"echo x > '{a / 'note.txt'}'"]),
        ("touch", ["touch", str(a / "note.txt")]),
        ("set an xattr", ["xattr", "-w", "com.example.floppy.label", "rosso", str(a / "note.txt")]),
    ]
    for label, cmd in tries:
        ok, s, err = mac_try(*cmd)
        record(f"t17 read-only disk from the Mac: {label}", "accepted" if ok else f"refused: {err[0]}",
               "refused within 2 s", not ok and s < 2, f"({s:.2f} s)")
    if os.environ.get("FINDER") == "1":  # drives the real Finder: macOS asks once to allow automating it
        src = Path(os.environ.get("TMPDIR", "/tmp")) / "floppy-finder-probe.txt"
        src.write_text("probe\n")
        for label, script in (
                ("Finder copy", f'tell application "Finder" to duplicate (POSIX file "{src}" as alias) to (POSIX file "{a}" as alias)'),
                ("Finder delete", f'tell application "Finder" to delete (POSIX file "{a / "note.txt"}" as alias)'),
                ("Finder rename", f'tell application "Finder" to set name of (POSIX file "{a / "note.txt"}" as alias) to "x.txt"')):
            ok, s, err = mac_try("/usr/bin/osascript", "-e", script)
            record(f"t17 read-only disk, {label}", "accepted" if ok else f"refused: {err[0]}", "refused", not ok, f"({s:.2f} s)")
        src.unlink()
    inside = in_drive("{ echo x > /mnt/drive/Archivio/x; } 2>&1; true").stdout.strip()
    record("t17h read-only disk inside the VM: create", inside or "accepted", "Read-only file system",
           "Read-only file system" in inside)
    ok_read = (a / "note.txt").read_text().strip() == "archived" and len(os.listdir(a / "clips")) == 21
    record("t17i reads after the refused writes", "ok" if ok_read else "failed", "ok", ok_read)


def t18():
    """Point 4: one bucket per disk, the device's key granted per bucket."""
    after = object_count(2)
    record("t18a read-only mount wrote no object to its bucket", f"{STATE['objects2']} -> {after} objects",
           "unchanged", after == STATE["objects2"])
    s3err = in_drive("grep -ciE 'accessdenied|forbidden|403|upload' /var/log/jfs-d2.log").stdout.strip()
    record("t18b read-only mount with a read-only key: S3 errors in its log", s3err, "0", s3err == "0")
    k = DEVICE_KEY
    checks = [
        ("read-only bucket: list", s3(k, "GET", f"{BUCKET.format(2)}?list-type=2"), "200"),
        ("read-only bucket: put", s3(k, "PUT", f"{BUCKET.format(2)}/probe", data="x"), "403"),
        ("read-only bucket: delete", s3(k, "DELETE", f"{BUCKET.format(2)}/probe"), "403"),
        ("read-write bucket: put", s3(k, "PUT", f"{BUCKET.format(1)}/probe", data="x"), "200"),
        ("read-write bucket: delete", s3(k, "DELETE", f"{BUCKET.format(1)}/probe"), "204"),
    ]  # the bucket with no access is checked by t23, once Riservato is hidden
    for label, got, want in checks:
        record(f"t18 device key, {label}", f"HTTP {got}", f"HTTP {want}", got == want)


def t19():
    """Point 2, tampered: the device mounts the read-only disk without --read-only."""
    n = DISKS["Archivio"]
    t = time.perf_counter()
    r = exec_in("drive-p", f"mkdir -p /tamper; juicefs mount '{meta_url(n)}' /tamper {mount_flags(n, False)} "
                "--log /var/log/jfs-tamper.log 2>&1 | tail -3", env=s3_env(DEVICE_KEY), check=False, timeout=120)
    s = time.perf_counter() - t
    up = in_drive("mountpoint -q /tamper").returncode == 0
    why = in_drive("grep -m1 -oE 'new session.*|NOPERM[^\\[]*' /var/log/jfs-tamper.log").stdout.strip()
    if up:
        w = in_drive("timeout 20 sh -c 'echo x > /tamper/tampered' 2>&1; true").stdout.strip()
        in_drive("umount /tamper")
        record("t19a tampered mount without --read-only", f"mounted, write: {w or 'accepted'}", "refused", bool(w))
    else:
        record("t19a tampered mount without --read-only", f"refused in {s:.1f} s: {why[:120]}", "refused", True)
    attempts = [("SET", "x", "1"), ("HSET", "i1", "x", "1"), ("DEL", "setting"), ("INCR", "nextinode"),
                ("EVAL", "return redis.call('set','x','1')", "0"), ("FLUSHDB",), ("CONFIG", "SET", "appendonly", "no"),
                ("SCRIPT", "FLUSH"), ("ACL", "SETUSER", DEVICE, "+@all"), ("RESTORE", "x", "0", "x")]
    accepted = [a[0] for a in attempts if "NOPERM" not in as_device(n, "-n", "1", *a, via="valkey-p2")]
    record("t19b device user on the read-only instance: direct writes", f"{len(attempts) - len(accepted)}/{len(attempts)} refused",
           "all refused", not accepted, f"accepted: {accepted}" if accepted else "(NOPERM)")
    reads = in_drive("cat /mnt/drive/Archivio/note.txt").stdout.strip()
    record("t19c read-only mount still healthy after the tampering", f"mode {mode('Archivio')}, read {reads!r}",
           "mode 1, 'archived'", mode("Archivio") == "1" and reads == "archived")


def t20():
    """A permission lowered from read-write to read while the disk is mounted read-write."""
    n = DISKS["Progetti"]
    set_user(n, "r")
    try:
        t = time.perf_counter()
        w = in_drive("timeout 60 sh -c 'echo x > /mnt/drive/Progetti/after-downgrade' 2>&1; echo rc=$?").stdout.strip()
        s = time.perf_counter() - t
        record("t20a lowered to read, mount still read-write: write inside the VM", w.replace("\n", " ")[:140],
               "refused within 2 s", "rc=0" not in w and s < 2, f"({s:.2f} s)")
        ok, s, err = mac_try("cp", "/etc/hosts", str(MNT / "Progetti" / "hosts-after-downgrade"))
        record("t20b same, copy from the Mac", "accepted" if ok else f"refused: {err[0]}", "refused within 2 s",
               not ok and s < 2, f"({s:.2f} s)")
        time.sleep(15)  # past a session heartbeat, which now fails
        reads = in_drive("ls /mnt/drive/Progetti/media | wc -l").stdout.strip()
        record("t20c same, mount healthy after a refused heartbeat", f"mode {mode('Progetti')}, {reads} files listed",
               "mode 1, 300", mode("Progetti") == "1" and reads == "300",
               in_drive(f"grep -m1 -oE 'Refresh session.*' /var/log/jfs-d{n}.log").stdout.strip()[:120])
    finally:
        set_user(n, "rw")
    w = in_drive("timeout 30 sh -c 'echo x > /mnt/drive/Progetti/after-restore' 2>&1; echo rc=$?").stdout.strip()
    record("t20d raised back to read-write: writes work again without a remount", w, "rc=0", w.endswith("rc=0"))


def t21():
    """Point 6: offline is per disk; the read-only disk goes offline alone and comes back."""
    toxi("toggle", "disk2")
    try:
        s = wait_mode("Archivio", 2, limit=30)
        reads = in_drive("timeout 20 cat /mnt/drive/Archivio/note.txt").stdout.strip()
        other = mode("Progetti")
        w = in_drive("timeout 20 sh -c 'echo y > /mnt/drive/Progetti/while-archivio-off' 2>&1; echo rc=$?").stdout.strip()
    finally:
        toxi("toggle", "disk2")
    back = wait_mode("Archivio", 1, limit=60)
    record("t21a read-only disk offline: mode 2, reads go on", f"offline in {s:.1f} s, read {reads!r}", "offline, 'archived'",
           reads == "archived")
    record("t21b the other disks are not affected", f"Progetti mode {other}, write {w}", "mode 1, rc=0",
           other == "1" and w.endswith("rc=0"))
    record("t21c read-only disk back online", f"mode 1 after {back:.1f} s", "< 10 s", back < 10)


def t22():
    """Point 6: read-your-writes on a disk with its own master."""
    r = in_drive("""set -e; d=/mnt/drive/Progetti/ryw; mkdir -p $d; bad=0
for i in $(seq 1 300); do echo "v$i" > $d/f; [ "$(cat $d/f)" = "v$i" ] || bad=$((bad+1));
  mv $d/f $d/g; [ -e $d/g ] && [ ! -e $d/f ] || bad=$((bad+1)); mv $d/g $d/f; done; echo $bad""", timeout=1800)
    bad = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "error"
    record("t22 read-your-writes, 300 write/rename/read cycles", f"{bad} stale reads", "0", bad == "0")


def t23():
    """Point 3: a disk hidden from the person, live: no mount, no replica, no user, no folder."""
    unmount_disk("Riservato", remove_folder=True)
    compose("rm", "-sf", "valkey-p3")
    set_user(3, None)
    time.sleep(2)
    names = sorted(os.listdir(MNT))
    record("t23a hidden disk: the folder is gone from the Mac's share", str(names), "Archivio, Progetti",
           names == ["Archivio", "Progetti"])
    out = as_device(3, "PING")
    record("t23b hidden disk: the device's user cannot connect", out.splitlines()[-1][:100], "refused", "PONG" not in out)
    start_replicas([3])  # a tampered device starting the replica anyway
    time.sleep(6)
    info = replica_cli(3, "INFO", "replication")
    link = re.search(r"master_link_status:(\w+)", info)
    keys = replica_cli(3, "-n", "1", "DBSIZE")
    record("t23c hidden disk: a replica started anyway gets nothing", f"link {link.group(1) if link else '?'}, {keys} keys",
           "down, 0 keys", bool(link) and link.group(1) == "down" and keys == "0")
    compose("rm", "-sf", "valkey-p3")
    set_device_grants({3: None})
    for label, got in (("list", s3(DEVICE_KEY, "GET", f"{BUCKET.format(3)}?list-type=2")),
                       ("get", s3(DEVICE_KEY, "GET", f"{BUCKET.format(3)}/probe"))):
        record(f"t23d hidden disk: device key, no-access bucket: {label}", f"HTTP {got}", "HTTP 403", got == "403")


def t24():
    """DECISIONS #20: nothing can be written at the top of the share, where it would land on the VM's disk."""
    for label, cmd in (("create a folder", ["mkdir", str(MNT / "top-level")]),
                       ("copy a file", ["cp", "/etc/hosts", str(MNT / "hosts")])):
        ok, s, err = mac_try(*cmd)
        record(f"t24 share root ({os.environ.get('ROOT_MODE', 'chmod')}, Samba as "
               f"{os.environ.get('SMB_FORCE_USER', 'root')}): {label}", "accepted" if ok else f"refused: {err[0]}",
               "refused", not ok, f"({s:.2f} s)")
    in_drive("rm -rf /mnt/drive/top-level /mnt/drive/hosts 2>/dev/null; true")


def t26():
    """A volume written before Samba ran unprivileged: root's files, then edited from the Mac."""
    # Written by a mount with no squash, as root, like today's single-user volumes.
    admin_mount(1, "/seed/1", "mkdir -p legacy/sub && for f in a b c; do echo old > legacy/$f; done; "
                "echo old > legacy/sub/d; chmod 0644 legacy/a legacy/b legacy/c; chmod 0755 legacy legacy/sub")
    time.sleep(1)
    d = MNT / "Progetti" / "legacy"
    tries = [
        ("overwrite a root file", ["sh", "-c", f"echo new > '{d / 'a'}'"]),
        ("rename a root file", ["mv", str(d / "b"), str(d / "b2")]),
        ("delete a root file", ["rm", str(d / "c")]),
        ("create in a root folder", ["cp", "/etc/hosts", str(d / "sub" / "hosts")]),
        ("delete a root folder", ["rm", "-r", str(d / "sub")]),
    ]
    for label, cmd in tries:
        ok, s, err = mac_try(*cmd)
        record(f"t26 squash={SQUASH or 'none'}, Samba as {os.environ.get('SMB_FORCE_USER', 'root')}: {label}",
               "ok" if ok else f"refused: {err[0]}", "ok", ok, f"({s:.2f} s)")
    mac_try("cp", "/etc/hosts", str(MNT / "Progetti" / "from-mac"))  # the volume's root is 0777
    # What a later mount with no squash sees: the stored owners.
    owners = {}
    admin_mount(1, "/seed/1", "stat -c '%n %u:%g' legacy/a from-mac > /tmp/owners 2>&1; true")
    for line in in_drive("cat /tmp/owners").stdout.splitlines():
        if len(line.split()) == 2:
            owners[line.split()[0].split("/")[-1]] = line.split()[1]
    record("t26 owners stored in the metadata, seen with no squash", str(owners),
           "a stays 0:0", owners.get("a") == "0:0",
           "(from-mac is the squashed account)" if SQUASH else "")


def t25():
    """Point 5: replication cost of a VM start with three disks of 10,000 files each."""
    files = int(os.environ.get("FILES", "10000"))
    set_user(3, "rw")
    set_latency(0)
    try:
        def seed(n):
            admin_mount(n, f"/seed/{n}", f"mkdir -p bulk && cd bulk && seq -w 1 {files} | "
                        "xargs -P 16 -I{} sh -c 'head -c 1024 /dev/urandom > f{}.dat'")
        ts = [threading.Thread(target=seed, args=(n,)) for n in DISKS.values()]
        s, _ = timed(lambda: ([t.start() for t in ts], [t.join() for t in ts]))
    finally:
        set_latency(LATENCY_MS)
    print(f"      seeded {files} files on each disk in {s:.0f} s", flush=True)
    for n in DISKS.values():
        size = compose("exec", "-T", "-e", "REDISCLI_AUTH", f"valkey-disk{n}", "sh", "-c",
                       "valkey-cli --rdb /tmp/size.rdb >/dev/null 2>&1; stat -c %s /tmp/size.rdb; rm -f /tmp/size.rdb",
                       env={"REDISCLI_AUTH": VALKEY_PW}).stdout.strip()
        STATE[f"rdb{n}"] = int(size)
    # A VM start: the share and every disk's mount go down, the replicas restart empty.
    mac_unmount()
    for name in ("Progetti", "Archivio"):
        unmount_disk(name)
    compose("rm", "-sf", "valkey-p1", "valkey-p2", "valkey-p3")
    sent0 = {n: int(re.search(r"total_net_repl_output_bytes:(\d+)", admin(n, "INFO", "stats")).group(1))
             for n in DISKS.values()}
    # Polled inside drive-p: a docker exec per probe would cost more than what it measures.
    poll = threading.Thread(target=lambda: STATE.update(poll=in_drive(
        "for _ in $(seq 1 1200); do all=1; for d in Progetti Archivio Riservato; do "
        "if [ ! -e /tmp/ready-$d ]; then if grep -q '^juicefs_meta_replica_mode 1' /mnt/drive/$d/.stats 2>/dev/null; "
        "then echo \"$d $(date +%s.%N)\"; touch /tmp/ready-$d; else all=0; fi; fi; done; "
        "[ $all = 1 ] && break; sleep 0.05; done; rm -f /tmp/ready-*", timeout=120).stdout))
    t0 = time.time()
    poll.start()
    start_replicas(DISKS.values())
    perms = {"Progetti": "rw", "Archivio": "r", "Riservato": "rw"}
    for name in perms:  # one at a time: each remounts the share's root read-write
        in_drive(f"/perdisk-entrypoint.sh folder add {name}", check=True)
    mounts = [threading.Thread(target=mount_disk, args=(name, p), kwargs={"wait": False, "folder": False})
              for name, p in perms.items()]
    for t in mounts:
        t.start()
    for t in mounts:
        t.join()
    poll.join()
    ready = {line.split()[0]: float(line.split()[1]) - t0 for line in STATE["poll"].splitlines() if len(line.split()) == 2}
    for name, n in DISKS.items():
        sent = int(re.search(r"total_net_repl_output_bytes:(\d+)", admin(n, "INFO", "stats")).group(1)) - sent0[n]
        rss = re.search(r"used_memory_rss:(\d+)", replica_cli(n, "INFO", "memory"))
        logs = compose("logs", "-t", f"valkey-p{n}").stdout
        done = re.search(r"(\d{4}-\d\d-\d\dT[\d:.]+)Z.*(?:MASTER|PRIMARY) <-> REPLICA sync: Finished with success", logs)
        synced = datetime.fromisoformat(done.group(1)[:26]).replace(tzinfo=timezone.utc).timestamp() - t0 if done else -1
        record(f"t25 disk {n} ({files} files): full sync at start", f"replica synced in {synced:.1f} s, mode 1 in "
               f"{ready.get(name, -1):.1f} s", "mode 1 < 10 s", 0 < ready.get(name, 99) < 10,
               f"(RDB {STATE[f'rdb{n}'] / 1e6:.1f} MB, sent over the link {sent / 1e6:.1f} MB, replica RSS "
               f"{int(rss.group(1)) / 2**20 if rss else -1:.0f} MB)")
    total = max(ready.values()) if len(ready) == 3 else -1
    record("t25 VM start with three disks: all in mode 1", f"{total:.1f} s", "< 15 s", 0 < total < 15)
    mac_mount()


TESTS = [t14, t15, t16, t17, t18, t19, t20, t21, t22, t23, t24, t26, t25]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tests", nargs="*")
    a = ap.parse_args()
    chosen = [t for t in TESTS if not a.tests or t.__name__ in a.tests]
    random.seed(7)
    try:
        setup()
        for t in chosen:
            try:
                t()
            except Exception as e:  # a crashed test is a failed test, reported with its cause
                record(t.__name__, "crashed", "-", False, str(e)[:300])
    finally:
        teardown()
    failed = [r for r in RESULTS if not r[3]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed ({LATENCY_MS} ms)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
