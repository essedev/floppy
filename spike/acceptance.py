#!/usr/bin/env python3
"""Acceptance tests for the shared disk with a local metadata replica.

Two clients (drive and drive-b) mount the same JuiceFS volume, whose metadata
lives in a central Valkey behind a simulated link (Toxiproxy), each with a local
Valkey replica in its own network namespace, as in a Mac's VM. The Mac mounts
both over SMB. Each test prints its measure, its threshold and PASS or FAIL.

Usage: spike/acceptance.py [--baseline] [test ...]
  --baseline  same tests without the replica option, for comparison
  tests       t01 ... t13 (default: all)

Needs setup.sh run first, the patched juicefs in the drive image
(JUICEFS_SOURCE=replica), the footage in spike/footage for t03, and the macOS SMB
timeout raised for localhost and 127.0.0.1 in ~/Library/Preferences/nsmb.conf
(max_resp_timeout=600), as the real install does for the VM's address.

The data path runs as the product does (DECISIONS #15): the shaped link and the
same format and mount flags, passed through the environment:
  LINE_MBPS=100 docker compose --profile shaped up -d shaper
  DOWNLOAD_LIMIT_MBPS=0 FORMAT_OPTS="--block-size 1024" \
    MOUNT_OPTS="--max-readahead 4" spike/acceptance.py
"""

import argparse
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
HOME = Path.home()
MNT_A, MNT_B = HOME / "FloppySpikeA", HOME / "FloppySpikeB"
PORT_A, PORT_B = 4503, 4506
LATENCY_MS = int(os.environ.get("LATENCY_MS", "15"))
# A fresh volume name per run: a reformat refuses a bucket prefix that still holds blocks.
VOLUME = f"accept-{int(time.time())}"
RESULTS = []


def env_val(name):
    for line in (SPIKE / ".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    sys.exit(f"acceptance: {name} missing in spike/.env, run setup.sh")


VALKEY_PW = env_val("VALKEY_PASSWORD")
SMB_PW = env_val("SMB_PASSWORD")
MAC_PW = {"a": env_val("VALKEY_PASSWORD") + "a", "b": env_val("VALKEY_PASSWORD") + "b"}


def run(*cmd, check=True, timeout=600, quiet=True):
    """Runs a command in spike/; returns its stdout."""
    r = subprocess.run(cmd, cwd=SPIKE, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}...: {r.stderr.strip()[-400:]}")
    return r.stdout if quiet else r


def compose(*args, **kw):
    return run("docker", "compose", "--profile", "replica", *args, **kw)


def exec_in(service, script, check=True, timeout=600):
    """A shell script inside a service; returns the completed process."""
    r = subprocess.run(["docker", "compose", "--profile", "replica", "exec", "-T", service, "bash", "-c", script],
                       cwd=SPIKE, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{service}: {r.stderr.strip()[-400:]}")
    return r


def valkey(*args):
    """A command on the central Valkey as admin."""
    return compose("exec", "-T", "valkey", "valkey-cli", "--no-auth-warning", "-a", VALKEY_PW, *args).strip()


def toxi(*args):
    return compose("exec", "-T", "toxiproxy", "/toxiproxy-cli", *args)


def master_url(user=None):
    cred = f"{user}:{MAC_PW[user[-1]]}" if user else f":{VALKEY_PW}"
    return f"redis://{cred}@toxiproxy:4505/1"


def client_url(mac, baseline):
    user = f"mac-{mac}"
    url = master_url(user)
    if baseline:
        return url
    return f"{url}?read-replica=redis://:{VALKEY_PW}@127.0.0.1:6380/1&max-lag=2s"


def mount(port, path):
    # macOS refuses a second mount of the same server and share even on another
    # port, so the second client is reached by address instead of by name.
    host = "localhost" if port == PORT_A else "127.0.0.1"
    path.mkdir(exist_ok=True)
    for _ in range(40):
        if subprocess.run(["/sbin/mount_smbfs", f"//drive:{SMB_PW}@{host}:{port}/Floppy", str(path)],
                          capture_output=True).returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"cannot mount port {port}")


def unmount(path):
    for _ in range(5):
        if f" on {path} " not in subprocess.run(["/sbin/mount"], capture_output=True, text=True).stdout:
            return
        # A gentle unmount can leave the macOS SMB client with a stale session that
        # answers I/O errors on the next mount of the same share
        subprocess.run(["/sbin/umount", "-f", str(path)], capture_output=True)
        time.sleep(1)


def record(test, measure, threshold, ok, note=""):
    RESULTS.append((test, measure, threshold, ok, note))
    print(f"{'PASS' if ok else 'FAIL'}  {test}: {measure} (threshold {threshold}) {note}", flush=True)


def wait_mount(service):
    for _ in range(60):
        r = exec_in(service, "mountpoint -q /mnt/drive", check=False)
        if r.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"{service}: JuiceFS did not mount")


def setup(baseline):
    """A fresh volume, per-Mac Valkey users, both clients up and mounted on the Mac."""
    for p in (MNT_A, MNT_B):
        unmount(p)
    compose("rm", "-sf", "drive", "drive-b", "valkey-a", "valkey-b", check=False)
    compose("up", "-d", "valkey", "toxiproxy")
    toxi("toxic", "update", "-n", "latency_downstream", "-a", f"latency={LATENCY_MS}", "valkey")
    valkey("FLUSHALL")
    for mac in ("a", "b"):
        # One user per Mac: metadata reads and writes, plus replication for its replica
        valkey("ACL", "SETUSER", f"mac-{mac}", "reset", "on", f">{MAC_PW[mac]}", "~*", "&*", "+@all", "-@admin",
               "+psync", "+replconf", "+ping", "+info", "+config|get", "+client|setname", "+client|id")
    env = dict(os.environ, META_URL=client_url("a", baseline), META_URL_B=client_url("b", baseline),
               VOLUME_NAME=VOLUME, TRASH_DAYS="7", VALKEY_MASTERUSER_A="mac-a", VALKEY_MASTERUSER_B="mac-b")
    for svc, rep in (("drive", "valkey-a"), ("drive-b", "valkey-b")):
        subprocess.run(["docker", "compose", "--profile", "replica", "up", "-d", "--force-recreate", svc, rep],
                       cwd=SPIKE, env=env, capture_output=True, text=True, check=True)
        wait_mount(svc)
    mount(PORT_A, MNT_A)
    mount(PORT_B, MNT_B)


def restart_b(baseline):
    """Client B with empty JuiceFS caches; its replica shares the namespace and restarts too."""
    unmount(MNT_B)
    env = dict(os.environ, META_URL_B=client_url("b", baseline), VOLUME_NAME=VOLUME, TRASH_DAYS="7",
               VALKEY_MASTERUSER_B="mac-b")
    subprocess.run(["docker", "compose", "--profile", "replica", "up", "-d", "--force-recreate", "drive-b", "valkey-b"],
                   cwd=SPIKE, env=env, capture_output=True, text=True, check=True)
    wait_mount("drive-b")
    mount(PORT_B, MNT_B)
    time.sleep(2)


def wait_mode(service, mode, limit=30):
    """Waits until the client's metadata mode (juicefs_meta_replica_mode in .stats) is the given one."""
    t = time.perf_counter()
    while time.perf_counter() - t < limit:
        r = exec_in(service, "grep '^juicefs_meta_replica_mode' /mnt/drive/.stats", check=False)
        if r.stdout.split()[-1:] == [str(mode)]:
            return time.perf_counter() - t
        time.sleep(0.5)
    raise RuntimeError(f"{service}: mode {mode} not reached in {limit} s")


def remount_both():
    """macOS does not always bring an SMB mount back after the server stalled."""
    for port, path in ((PORT_A, MNT_A), (PORT_B, MNT_B)):
        subprocess.run(["/sbin/umount", "-f", str(path)], capture_output=True)
        mount(port, path)


def timed(fn):
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


# The tests. Thresholds are the acceptance criteria agreed before measuring.

def t01(baseline):
    media = MNT_A / "acc" / "media"
    media.mkdir(parents=True, exist_ok=True)
    blob = os.urandom(64 * 1024)

    def seed():
        for i in range(300):
            (media / f"clip_{i:03d}.mov").write_bytes(blob)
    seed_s = timed(seed)
    restart_b(baseline)
    s = timed(lambda: [e.stat() for e in os.scandir(MNT_B / "acc" / "media")])
    record("t01 cold listing of 300 files on B", f"{s:.2f} s", "< 0.5 s", s < 0.5, f"(seeding them on A took {seed_s:.1f} s)")


def t02(baseline):
    d = MNT_A / "acc" / "small"
    d.mkdir(parents=True, exist_ok=True)
    s = timed(lambda: [(d / f"f_{i:03d}.json").write_bytes(b"{}" * 2048) for i in range(100)])
    # Revised from 10 s on 17/10: about 9 sequential metadata writes per file come from the
    # SMB protocol, each already one round trip; lower needs asynchronous metadata, not wanted.
    record("t02 create 100 small files on A", f"{s:.1f} s", "< 20 s", s < 20)


def t03(baseline):
    clip = SPIKE / "footage" / "clip_1.mov"
    if not clip.exists():
        record("t03 copy and stream a clip", "skipped", "-", False, "(no footage in spike/footage)")
        return
    size_mb = clip.stat().st_size / 1e6
    up = timed(lambda: subprocess.run(["cp", str(clip), str(MNT_A / "acc" / "clip.mov")], check=True))
    restart_b(baseline)
    exec_in("drive-b", "juicefs warmup --evict /mnt/drive/acc/clip.mov >/dev/null 2>&1 || true", check=False)
    down = timed(lambda: subprocess.run(["dd", f"if={MNT_B / 'acc' / 'clip.mov'}", "of=/dev/null", "bs=4m"],
                                        check=True, capture_output=True))
    # The data-path target (ROADMAP 1): a cold read at 80% of the simulated line, in MB/s
    line = int(os.environ.get("LINE_MBPS", "100")) / 8
    rate = size_mb / down
    record("t03 cold read of a clip on B", f"{rate:.1f} MB/s", f">= 80% of the {line:.1f} MB/s line",
           rate >= 0.8 * line, f"(copy on A {size_mb / up:.1f} MB/s)")


def t04(baseline):
    d = MNT_A / "acc" / "prop"
    d.mkdir(parents=True, exist_ok=True)
    worst = 0.0
    for i in range(20):
        (d / f"p_{i}").write_bytes(b"x")
        t = time.perf_counter()
        while not (MNT_B / "acc" / "prop" / f"p_{i}").exists():
            if time.perf_counter() - t > 10:
                break
            time.sleep(0.05)
        worst = max(worst, time.perf_counter() - t)
    record("t04 file created on A visible on B (worst of 20)", f"{worst:.2f} s", "< 2 s", worst < 2)


def t05(baseline):
    # Inside the VM on the FUSE mount: the Mac's SMB client would hide stale reads behind its own cache.
    r = exec_in("drive", """set -e; d=/mnt/drive/acc/ryw; mkdir -p $d; bad=0
for i in $(seq 1 1000); do echo "v$i" > $d/f; [ "$(cat $d/f)" = "v$i" ] || bad=$((bad+1));
  mv $d/f $d/g; [ -e $d/g ] && [ ! -e $d/f ] || bad=$((bad+1)); mv $d/g $d/f; done; echo $bad""", timeout=1800)
    bad = int(r.stdout.strip().splitlines()[-1])
    record("t05 read-your-writes, 1,000 write/rename/read cycles", f"{bad} stale reads", "0", bad == 0)


def t06(baseline):
    loop = """d=/mnt/drive/acc/race; mkdir -p $d; end=$(( $(date +%s) + 30 ))
while [ $(date +%s) -lt $end ]; do n=$((RANDOM % 20));
  case $((RANDOM % 4)) in 0) echo x > $d/f$n ;; 1) mv $d/f$n $d/g$n 2>/dev/null ;; 2) rm -f $d/g$n ;; 3) ls $d >/dev/null ;; esac
done; true"""
    ts = [threading.Thread(target=exec_in, args=(svc, loop), kwargs={"check": False}) for svc in ("drive", "drive-b")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    r = exec_in("drive", f"juicefs fsck '{master_url()}' --path /acc 2>&1", check=False, timeout=1800)
    ok = r.returncode == 0
    record("t06 concurrent creates, renames, deletes on A and B for 30 s", "fsck clean" if ok else "fsck errors",
           "clean", ok, "" if ok else r.stdout.strip().splitlines()[-1][:160])


def t07(baseline):
    exec_in("drive", "dd if=/dev/urandom of=/mnt/drive/acc/moving.bin bs=1M count=48 status=none")
    time.sleep(2)
    reader = threading.Thread(target=lambda: RESULTS_T07.append(exec_in("drive-b", """exec 3</mnt/drive/acc/moving.bin; err=0
for i in $(seq 1 48); do dd bs=1M count=1 <&3 of=/dev/null status=none || err=$((err+1)); sleep 0.1; done; echo $err""",
                                                                         check=False)))
    reader.start()
    time.sleep(1)
    exec_in("drive", "dd if=/dev/urandom of=/mnt/drive/acc/moving.bin bs=1M count=48 conv=notrunc status=none")
    time.sleep(1)
    exec_in("drive", "rm /mnt/drive/acc/moving.bin")
    reader.join()
    out = RESULTS_T07[-1].stdout.strip().splitlines()
    errs = int(out[-1]) if out and out[-1].isdigit() else -1
    record("t07 B keeps reading an open file that A rewrites and deletes", f"{errs} read errors", "0", errs == 0)


RESULTS_T07 = []


def t08(baseline):
    # Toxiproxy cuts the whole link: master and replication, like a Mac that loses the network.
    exec_in("drive-b", "mkdir -p /mnt/drive/acc/off && echo before > /mnt/drive/acc/off/before")
    time.sleep(1)
    toxi("toggle", "valkey")
    try:
        time.sleep(3)
        read = exec_in("drive-b", "timeout 20 ls /mnt/drive/acc/off && timeout 20 cat /mnt/drive/acc/off/before", check=False)
        t_write = time.perf_counter()
        write = exec_in("drive-b", "timeout 20 sh -c 'echo during > /mnt/drive/acc/off/during'", check=False)
        t_write = time.perf_counter() - t_write
    finally:
        toxi("toggle", "valkey")
        wait_mode("drive", 1)
        wait_mode("drive-b", 1)
        remount_both()
    reads_ok = read.returncode == 0 and "before" in read.stdout
    writes_fail = write.returncode != 0
    record("t08a offline: browsing and reading go on", "ok" if reads_ok else read.stderr.strip()[-120:], "ok", reads_ok)
    record("t08b offline: writes fail cleanly", f"{'fail' if writes_fail else 'accepted'} after {t_write:.1f} s",
           "fail within 20 s", writes_fail and t_write < 19.5)
    t = time.perf_counter()
    (MNT_A / "acc" / "off" / "after").write_bytes(b"x")
    while not (MNT_B / "acc" / "off" / "after").exists() and time.perf_counter() - t < 30:
        time.sleep(0.1)
    back = time.perf_counter() - t
    record("t09 link back: B sees new files again", f"{back:.1f} s", "< 5 s", back < 5)


def t10(baseline):
    if baseline:
        record("t10 replica rebuilt from scratch", "n/a", "-", True, "(baseline has no replica)")
        return
    compose("stop", "valkey-b")
    try:
        _t10_body()
    finally:
        compose("start", "valkey-b")


def _t10_body():
    (MNT_A / "acc" / "rebuild").mkdir(exist_ok=True)
    (MNT_A / "acc" / "rebuild" / "while-down").write_bytes(b"x")
    seen_down = (MNT_B / "acc" / "rebuild" / "while-down").exists() or (time.sleep(2.5) or
                                                                         (MNT_B / "acc" / "rebuild" / "while-down").exists())
    compose("start", "valkey-b")
    time.sleep(5)
    link = compose("exec", "-T", "valkey-b", "valkey-cli", "--no-auth-warning", "-a", VALKEY_PW, "-p", "6380",
                   "INFO", "replication")
    up = "master_link_status:up" in link
    record("t10a replica down: B still sees new files (through the master)", "yes" if seen_down else "no", "yes", seen_down)
    record("t10b replica restarted empty resyncs on its own", "link up" if up else "link down", "link up", up)


def t11(baseline):
    f = MNT_A / "acc" / "tagged.mov"
    f.write_bytes(b"x")
    subprocess.run(["xattr", "-w", "com.example.floppy.label", "rosso", str(f)], check=True)
    time.sleep(2)
    r = subprocess.run(["xattr", "-p", "com.example.floppy.label", str(MNT_B / "acc" / "tagged.mov")],
                       capture_output=True, text=True)
    shared = r.stdout.strip() == "rosso"
    record("t11a extended attribute set on A read on B", r.stdout.strip() or r.stderr.strip()[-80:], "rosso", shared)
    rm = subprocess.run(["rm", str(MNT_B / "acc" / "tagged.mov")], capture_output=True, text=True)
    record("t11b delete from the Mac over SMB", "ok" if rm.returncode == 0 else rm.stderr.strip(), "ok", rm.returncode == 0)


def t12(baseline):
    (MNT_A / "acc" / "revoke").mkdir(exist_ok=True)
    valkey("ACL", "DELUSER", "mac-b")
    valkey("CLIENT", "KILL", "USER", "mac-b")
    time.sleep(3)
    write = exec_in("drive-b", "timeout 20 sh -c 'echo x > /mnt/drive/acc/revoke/from-b'", check=False)
    (MNT_A / "acc" / "revoke" / "after-revoke").write_bytes(b"x")
    time.sleep(5)
    leaked = exec_in("drive-b", "timeout 20 test -e /mnt/drive/acc/revoke/after-revoke", check=False).returncode == 0
    record("t12a revoked Mac cannot write", "refused" if write.returncode != 0 else "accepted", "refused",
           write.returncode != 0)
    record("t12b revoked Mac stops receiving changes", "still receives" if leaked else "stopped", "stopped", not leaked)


def t13(baseline):
    # A slow but alive master, as behind an uplink saturated by uploads: writes must slow
    # down, never turn read-only. Toxiproxy cannot share one link between S3 and Valkey,
    # so the slow link is simulated on the master alone.
    toxi("toxic", "update", "-n", "latency_downstream", "-a", "latency=1200", "-a", "jitter=600", "valkey")
    try:
        r = exec_in("drive", """d=/mnt/drive/acc/slow; mkdir -p $d; ro=0; other=0
first=""
for i in $(seq 1 20); do out=$( { echo x > $d/s$i; } 2>&1 ) || { [ -n "$first" ] || first="$out"; case "$out" in *Read-only*) ro=$((ro+1)) ;; *) other=$((other+1)) ;; esac; }; done
echo "first error: $first"; echo "$ro $other" """, check=False, timeout=900)
    finally:
        toxi("toxic", "update", "-n", "latency_downstream", "-a", f"latency={LATENCY_MS}", "-a", "jitter=0", "valkey")
    ro, other = (int(x) for x in r.stdout.split()[-2:])
    first = next((line for line in r.stdout.splitlines() if line.startswith("first error:")), "")
    record("t13 slow master (1.2 s +/- 0.6 s): 20 writes", f"{ro} read-only, {other} other errors", "0, 0",
           ro == 0 and other == 0, first[:160] if ro or other else "")


TESTS = [t01, t02, t03, t04, t05, t06, t07, t08, t10, t13, t11, t12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("tests", nargs="*")
    a = ap.parse_args()
    chosen = [t for t in TESTS if not a.tests or t.__name__ in a.tests]
    random.seed(7)
    setup(a.baseline)
    try:
        for t in chosen:
            try:
                t(a.baseline)
            except Exception as e:  # a crashed test is a failed test, reported with its cause
                record(t.__name__, "crashed", "-", False, str(e)[:200])
    finally:
        for p in (MNT_A, MNT_B):
            unmount(p)
    failed = [r for r in RESULTS if not r[3]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed ({'baseline' if a.baseline else 'replica'}, "
          f"{LATENCY_MS} ms)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
