#!/usr/bin/env python3
"""
Uploads under real lines, on the local stack and a development VM: big copies from the Mac
to a team disk while the VM's link behaves like an office line, a slow uplink with a router
that queues seconds of data, a fast one, one that drops for a minute. It answers whether a
copy survives, how much of the line it uses, how long the metadata waits behind the blocks,
and how much memory JuiceFS takes.

  deploy/local/upload-bench.py [--variant V]... [--profile P]... [--size-mb N]

Variants (the VM's mount and services; default: all, in this order):
  base      JuiceFS's defaults as the engine ships them (20 parallel uploads), no shaping
  uploads4  base with --max-uploads 4: a fixed cap
  uplink    base with floppy-uplink, the adaptive shaper

Profiles (the line, a netem queue on the VM's link to the stack, after any shaping):
  bloat40   40 Mbit/s up, 3 s of router buffer: the copy that broke (CYCLES 15)
  bloat10   10 Mbit/s up, 2 s of buffer: a slow office uplink
  short40   40 Mbit/s up, 0.3 s of buffer
  fast500   500 Mbit/s up, 0.1 s of buffer
  none      no line: the local path's own speed
  cut40     short40, with the link gone for 60 s from the 20th second

Each run copies a file of random bytes with /bin/cp to the disk "Upload bench", sized for
about 100 s on the line (256 MB to 2 GB; --size-mb overrides), then evicts it from the VM's
cache and reads it back from the bucket to compare checksums. It prints one line per run
with PASS or FAIL against these thresholds, and appends it to deploy/local/.state/
upload-bench.tsv:
  copy        /bin/cp exits 0 and the bytes read back from the bucket are the same
  commits     no failed JuiceFS commit nor I/O error in the VM's journal
  memory      no OOM kill in the VM, and at least 100 MB of it still available
  awake       the VM never stood still (Lima resyncing its clock after the Mac slept); the
              bench holds a caffeinate while it runs, but a closed lid still sleeps
  line use    sent at 80% of the line or more (shaped profiles, not cut40)
  wait        95th percentile of the round trip to the stack under 500 ms (the time a
              metadata command waits behind the blocks), not cut40

UPLINK_BLOAT_MS sets the uplink variant's queue threshold (floppy-uplink's BLOAT_MS).

Needs the stack up (deploy/local/stack.sh up) and the engine installed in FLOPPY_HOME
(default /tmp/fe2e, VM fe2e; see e2e.py). The first run creates the disk as the admin, gives
the admin access to it alone, and signs the VM in through the app's Core (the ignored Rust
test account::e2e_engine, left running for FLOPPY_E2E_MINUTES, default 600); the next runs
reuse the mounted disk. The variants patch /usr/local/sbin/floppy-disks in the VM and restart
the disk's mount; `floppy setup` on the development home puts the engine back.
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
# As stack.sh: the manual stack's project, state and port.
MANUAL = os.environ.get("STACK", "managed") == "manual"
STATE = HERE / (".state-manual" if MANUAL else ".state")
SERVER = "http://127.0.0.1:4593" if MANUAL else "http://127.0.0.1:4583"
API = f"{SERVER}/api/v1"
ADMIN = "admin@floppy.test"
CONTAINER = "floppy-local-manual-server-1" if MANUAL else "floppy-local-server-1"
HOME = os.environ.get("FLOPPY_HOME", "/tmp/fe2e")
VM = os.environ.get("FLOPPY_VM", "fe2e")
ENGINE = f"{HOME}/bin/floppy"
LIMACTL = f"{HOME}/lima/bin/limactl"
DISK = "Upload bench OVH" if MANUAL else "Upload bench"
# The real line's upload speed in Mbit/s, for the share of it a manual run uses.
LINE_MBPS = float(os.environ.get("LINE_MBPS", "0"))
FILES = Path(os.environ.get("BENCH_FILES", "/tmp/fbench/files"))
# The stack, as the VM reaches it (Lima's host address): the link the profiles shape.
STACK_IP = "192.168.5.2"

PROFILES = {
    # name: (line Mbit/s or 0 for none, router buffer in seconds, outage)
    "bloat40": (40, 3.0, False),
    "bloat10": (10, 2.0, False),
    "short40": (40, 0.3, False),
    "fast500": (500, 0.1, False),
    "none": (0, 0, False),
    "cut40": (40, 0.3, True),
}
VARIANTS = ("base", "uploads4", "uplink")
# A variant may combine its parts with "+": uploads4+uplink.
PART = re.compile(r"^(base|uploads4|uplink)$")


def run(*args: str, check: bool = True, timeout: float = 600, env: dict | None = None) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False,
                            env={**os.environ, **(env or {})})
    if check and result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-6:]
        raise RuntimeError(f"{' '.join(args[:5])} failed: " + " | ".join(tail))
    return result.stdout


def vm(script: str, timeout: float = 600, check: bool = True) -> str:
    return run(LIMACTL, "shell", "--workdir", "/", VM, "sudo", "sh", "-c", script,
               env={"LIMA_HOME": f"{HOME}/vms"}, timeout=timeout, check=check)


def engine_status() -> dict:
    return json.loads(run(ENGINE, "status", "--json", env={"FLOPPY_HOME": HOME, "FLOPPY_VM": VM}))


def wait(what: str, predicate, limit: float, every: float = 2.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass
        time.sleep(every)
    sys.exit(f"{what}: not within {limit:.0f} s")


class Admin:
    """The admin's web session, by the link in the server's log."""

    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        before = self.mails()
        self.call("POST", "/auth/sign-in", {"email": ADMIN})
        mail = wait("the sign-in mail", lambda: self.mails() > before and self.last_mail(), 20, 0.3)
        token = urllib.parse.unquote(re.search(r"token=(\S+)", mail).group(1))
        status, _ = self.call("POST", "/auth/magic-link", {"token": token})
        if status != 200:
            sys.exit(f"admin sign-in: HTTP {status}")

    @staticmethod
    def log() -> str:
        out = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True, check=False)
        return out.stdout + out.stderr

    def mails(self) -> int:
        return self.log().count(f"To: {ADMIN}\n")

    def last_mail(self) -> str:
        log = self.log()
        return "\n".join(log[log.rfind(f"To: {ADMIN}\n"):].splitlines()[:16])

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        req = urllib.request.Request(f"{API}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Origin": SERVER})
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


def prepare() -> tuple[str, str]:
    """The disk mounted on the development VM: its id and its path on the Mac."""
    if Path(HOME).resolve() == (Path.home() / ".floppy").resolve():
        sys.exit("FLOPPY_HOME is the real install: this bench runs only on a development home")
    status = engine_status()
    disk = next((d for d in status.get("disks") or [] if d["folder"] == DISK and d["state"] == "mounted"), None)
    if disk and status.get("mount_point"):
        return disk["id"], f"{status['mount_point']}/{DISK}"
    admin = Admin()
    _, disks = admin.call("GET", "/disks")
    found = next((d for d in disks if d["name"] == DISK), None)  # type: ignore[union-attr]
    if found is None:
        body: dict[str, object] = {"name": DISK, "trashDays": 1}
        if MANUAL:
            body["storage"] = storage()
        code, found = admin.call("POST", "/disks", body)
        if code != 201:
            sys.exit(f"create {DISK}: HTTP {code} {found}")
    disk_id = found["id"]  # type: ignore[index]
    wait(f"{DISK} ready", lambda: admin.call("GET", f"/disks/{disk_id}")[1]["state"] == "ready", 120)
    _, session = admin.call("GET", "/session")
    me = session["user"]["id"]  # type: ignore[index]
    for d in disks:  # type: ignore[union-attr]
        if d["id"] != disk_id:
            admin.call("PUT", f"/disks/{d['id']}/access/{me}", {"access": "none"})
    run(str(HERE / "stack.sh"), "hosts", env={"FLOPPY_HOME": HOME, "FLOPPY_VM": VM})
    STATE.mkdir(exist_ok=True)
    # Left open on purpose: the device process writes to it after this returns.
    log = open(STATE / "upload-bench-device.log", "w")  # noqa: SIM115
    env = {**os.environ, "FLOPPY_HOME": HOME, "FLOPPY_VM": VM, "FLOPPY_E2E_SERVER": SERVER,
           "FLOPPY_E2E_CONTAINER": CONTAINER, "FLOPPY_E2E_EMAIL": ADMIN,
           "FLOPPY_E2E_MINUTES": os.environ.get("FLOPPY_E2E_MINUTES", "600")}
    subprocess.Popen(["cargo", "test", "--quiet", "account::e2e_engine::e2e_engine_device_against_the_local_stack",
                      "--", "--ignored", "--nocapture", "--exact"],
                     cwd=ROOT / "app", env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"signing the VM in; the device's log is {log.name}", flush=True)

    def mounted():
        s = engine_status()
        d = next((x for x in s.get("disks") or [] if x["id"] == disk_id), None)
        others = [x for x in s.get("disks") or [] if x["id"] != disk_id]
        return d and d["state"] == "mounted" and not others and s.get("mount_point") and s
    s = wait(f"{DISK} mounted alone on the VM", mounted, 1800, 10)
    return disk_id, f"{s['mount_point']}/{DISK}"


def storage() -> dict[str, str]:
    """The manual disk's endpoint, bucket and key, from E2E_STORAGE_ENV, never printed."""
    path = os.environ.get("E2E_STORAGE_ENV")
    if not path:
        sys.exit("STACK=manual needs E2E_STORAGE_ENV: a file with BUCKET_URL, ACCESS_KEY, SECRET_KEY")
    values = dict(line.split("=", 1) for line in Path(path).read_text().splitlines()
                  if "=" in line and not line.lstrip().startswith("#"))
    endpoint, bucket = values["BUCKET_URL"].strip().rstrip("/").rsplit("/", 1)
    return {"endpoint": endpoint, "bucket": bucket, "accessKeyId": values["ACCESS_KEY"].strip(),
            "secretKey": values["SECRET_KEY"].strip()}


def bucket_ip() -> str:
    """The bucket endpoint's address as the VM resolves it."""
    host = urllib.parse.urlparse(storage()["endpoint"]).hostname
    return vm(f"getent ahostsv4 {host} | head -1 | cut -d' ' -f1").strip()


def test_file(mb: int) -> tuple[Path, str]:
    FILES.mkdir(parents=True, exist_ok=True)
    path = FILES / f"random-{mb}.mov"
    sums = FILES / f"random-{mb}.sha256"
    if not path.exists() or not sums.exists():
        with open(path, "wb") as f:
            f.writelines(os.urandom(1 << 20) for _ in range(mb))
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 24):
                digest.update(chunk)
        sums.write_text(digest.hexdigest())
    return path, sums.read_text().strip()


def set_variant(variant: str, disk_id: str, current: list[str]) -> None:
    """The mount options and services of a variant; the disk remounts only when they change."""
    parts = variant.split("+")
    uploads = "4" if "uploads4" in parts else ""
    vm("[ -f /run/floppy-disks.orig ] || cp /usr/local/sbin/floppy-disks /run/floppy-disks.orig; "
       "cp /run/floppy-disks.orig /usr/local/sbin/floppy-disks"
       + (f"; sed -i 's|--cache-large-write --prefetch 8|--cache-large-write --max-uploads {uploads} --prefetch 8|' "
          "/usr/local/sbin/floppy-disks; grep -q -- '--max-uploads' /usr/local/sbin/floppy-disks" if uploads else ""))
    if "uplink" in parts:
        # Manual: the metadata is on this Mac and the blocks go to the bucket, on another
        # device; the shaper follows the bucket's endpoint.
        reflector = bucket_ip() if MANUAL else ""
        # UPLINK_BLOAT_MS tries another queue threshold than the controller's default.
        bloat = os.environ.get("UPLINK_BLOAT_MS", "")
        conf = f"REFLECTORS={reflector}\\n" + (f"BLOAT_MS={bloat}\\n" if bloat else "")
        vm(f"printf '{conf}' > /etc/floppy/uplink.env; "
           "systemctl restart floppy-uplink && systemctl is-active floppy-uplink")
    else:
        vm("systemctl stop floppy-uplink 2>/dev/null; rm -f /etc/floppy/uplink.env; true")
    mount = [uploads]
    if current == mount:
        return
    before = vm("pgrep -f '^/usr/local/bin/juicefs mount' | head -1").strip()
    vm(f"systemctl restart floppy-disk@{disk_id}", timeout=180)

    # JuiceFS blanks its argv (it may hold secrets): the options are those of the patched
    # script, so a new process with the engine's memory limit is enough.
    def remounted() -> bool:
        pid = vm("pgrep -f '^/usr/local/bin/juicefs mount' | head -1").strip()
        env = vm(f"tr '\\0' '\\n' < /proc/{pid}/environ") if pid else ""
        return bool(pid) and pid != before and "GOMEMLIMIT=" in env and f"/mnt/drive/{DISK}" in vm("mount")
    wait("the disk remounted with the variant's options", remounted, 120)
    current[:] = mount


def line(profile: str) -> str:
    mbps, buffer_s, _ = PROFILES[profile]
    if not mbps:
        return "tc qdisc del dev eth0 root 2>/dev/null; true"
    packets = max(64, int(mbps * 1e6 * buffer_s / 8 / 1514))
    return f"tc qdisc replace dev eth0 root netem rate {mbps}mbit limit {packets}"


SAMPLER = r"""
exec > /run/bench/samples 2>&1
while :; do
  printf '%s %s %s %s\n' "$(date +%s.%N)" "$(ps -o rss= -C juicefs | sort -n | tail -1)" \
    "$(awk '/MemAvailable/{print $2}' /proc/meminfo)" "$(cat /run/floppy-uplink.json 2>/dev/null | tr -d ' ')"
  sleep 1
done
"""


def drift_s(line: str) -> float:
    """The clock drift Lima corrected in a journal line, in seconds (Go's "-1m26.7s", "101ms")."""
    m = re.search(r"drift was -?([0-9hms.µ]+)\)", line)
    if not m:
        return 0.0
    units = {"h": 3600, "m": 60, "s": 1, "ms": 1e-3, "µs": 1e-6}
    return sum(float(n) * units[u] for n, u in re.findall(r"([0-9.]+)(h|ms|µs|m|s)", m.group(1)))


def one(variant: str, profile: str, disk_id: str, mac_dir: str, size_mb: int | None) -> dict:
    mbps, _, cut = PROFILES[profile]
    mb = size_mb or (2048 if not mbps else min(2048, max(256, int(mbps / 8 * 100))))
    path, digest = test_file(mb)
    name = f"{variant}-{profile}-{time.strftime('%H%M%S')}.mov"
    # The round trip through the line's queue: to the stack, or to the real bucket.
    target = bucket_ip() if MANUAL else STACK_IP
    vm("mkdir -p /run/bench; cat > /run/bench/sampler.sh <<'EOF'\n" + SAMPLER + "EOF\n"
       f"{line(profile)}; "
       "systemd-run --unit=bench-sampler sh /run/bench/sampler.sh >/dev/null; "
       f"systemd-run --unit=bench-ping sh -c 'exec ping -n -D -i 0.2 {target} > /run/bench/ping 2>&1' >/dev/null"
       + (f"; systemd-run --unit=bench-cut sh -c 'sleep 20; tc qdisc change dev eth0 root netem rate {mbps}mbit "
          f"loss 100%; sleep 60; {line(profile)}' >/dev/null" if cut else ""))
    # To the microsecond: the variant's remount, just before, logs within the same second.
    since = vm("date '+%Y-%m-%d %H:%M:%S.%6N'").strip()
    start = time.monotonic()
    copy = subprocess.run(["/bin/cp", str(path), f"{mac_dir}/{name}"], capture_output=True, text=True, check=False)
    seconds = time.monotonic() - start
    vm("systemctl stop bench-sampler bench-ping bench-cut 2>/dev/null; tc qdisc del dev eth0 root 2>/dev/null; true")
    journal = vm(f"journalctl --since '{since}' --no-pager | grep -E 'commitThread@|input/output error|"
                 "connection timed out|Out of memory|Killed process|SyncTime: system time synchronized' "
                 "| grep -v 'sudo\\[' | cut -c1-200; true")
    pings = [float(m) for m in re.findall(r"time=([0-9.]+) ms", vm("cat /run/bench/ping"))]
    samples = vm("cat /run/bench/samples").splitlines()
    # Each second's memory and controller state, to read a run's shaping afterwards.
    traces = STATE / "upload-bench-traces"
    traces.mkdir(parents=True, exist_ok=True)
    (traces / f"{time.strftime('%Y%m%dT%H%M%S')}-{variant}-{profile}.txt").write_text("\n".join(samples) + "\n")
    rss = [int(s.split()[1]) for s in samples if len(s.split()) > 2 and s.split()[1].isdigit()]
    avail = [int(s.split()[2]) for s in samples if len(s.split()) > 2 and s.split()[2].isdigit()]
    rates = []
    for s in samples:
        parts = s.split(maxsplit=3)
        if len(parts) == 4 and parts[3].startswith("{"):
            rate = json.loads(parts[3]).get("rate_mbps")
            if rate:
                rates.append(rate)
    same = False
    if copy.returncode == 0:
        vm_path = f"/mnt/drive/{DISK}/{name}"
        out = vm(f"juicefs warmup --evict '{vm_path}' >/dev/null 2>&1; sha256sum '{vm_path}'", timeout=900)
        same = out.split()[0] == digest if out else False
    recovered = None
    if cut:
        # After the outage the disk must take writes again by itself.
        time.sleep(max(0.0, 80 - (time.monotonic() - start)))
        back = time.monotonic()
        probe = f"{mac_dir}/{name}.probe"
        while time.monotonic() - back < 180:
            try:
                with open(probe, "wb") as f:
                    f.write(os.urandom(1 << 20))
                os.remove(probe)
                recovered = round(time.monotonic() - back, 1)
                break
            except OSError:
                time.sleep(2)
    # A copy cut halfway keeps its file open on the Mac for a while.
    for _ in range(60):
        try:
            if os.path.exists(f"{mac_dir}/{name}"):
                os.remove(f"{mac_dir}/{name}")
            break
        except OSError:
            time.sleep(2)
    # A failed commit logs from commitThread@; a goroutine dump (JuiceFS's watchdog) only names it.
    commits = sum(1 for x in journal.splitlines() if re.search(r"commitThread@|input/output error|connection timed out", x))
    # Lima resyncs the VM's clock after the VM stood still: the Mac slept, and the run measured
    # that. It also resyncs drifts of a tenth of a second, which are noise.
    paused = sum(1 for x in journal.splitlines() if drift_s(x) >= 5)
    ooms = sum(1 for x in journal.splitlines() if re.search(r"Out of memory|Killed process", x))
    if MANUAL:
        mbps = LINE_MBPS
    sent = mb * 8 / seconds
    p95 = statistics.quantiles(pings, n=20)[18] if len(pings) >= 20 else (max(pings) if pings else 0.0)
    result = {
        "variant": variant, "profile": profile, "mb": mb, "exit": copy.returncode, "same": same,
        "seconds": round(seconds, 1), "mbps": round(sent, 1), "line_use": round(sent / mbps, 2) if mbps else None,
        "rtt_p50_ms": round(statistics.median(pings), 1) if pings else None, "rtt_p95_ms": round(p95, 1),
        "rtt_max_ms": round(max(pings), 1) if pings else None, "rss_max_mb": round(max(rss) / 1024) if rss else None,
        "free_min_mb": round(min(avail) / 1024) if avail else None,
        "shaper_mbps": round(statistics.median(rates), 1) if rates else None,
        "commit_errors": commits, "paused": paused, "oom": ooms, "recovered_s": recovered, "error": copy.stderr.strip()[:160],
    }
    # A copy across an outage fails by design (DECISIONS #6): what counts there is the memory
    # and the disk taking writes again.
    checks = {"memory": ooms == 0 and bool(avail) and min(avail) >= 100 * 1024, "awake": paused == 0}
    if cut:
        checks["recovers"] = recovered is not None
    else:
        checks["copy"] = copy.returncode == 0 and same
        checks["commits"] = commits == 0
    if mbps and not cut:
        checks["line use"] = sent >= 0.8 * mbps
    if (mbps or MANUAL) and not cut:
        checks["wait"] = p95 < 500
    result["checks"] = checks
    result["pass"] = all(checks.values())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", action="append",
                        help="base, uploads4, uplink, or parts joined with +")
    parser.add_argument("--profile", action="append", choices=list(PROFILES))
    parser.add_argument("--size-mb", type=int)
    args = parser.parse_args()
    bad = [v for v in args.variant or [] if not all(PART.match(p) for p in v.split("+"))]
    if bad:
        sys.exit(f"unknown variant {bad[0]}")
    if any("uplink" in v for v in args.variant or VARIANTS) and "floppy-uplink" not in vm("ls /etc/systemd/system/"):
        sys.exit("floppy-uplink is not installed in the VM: `floppy setup` on the development home first")
    if MANUAL and (args.profile or ["none"]) != ["none"]:
        sys.exit("STACK=manual runs only the profile none: the real line to the bucket")
    if MANUAL:
        args.profile = ["none"]
    # Idle sleep would pause the VM mid-copy and fail the run for a reason the app prevents
    # (app/src-tauri/src/awake.rs); it ends with this process.
    subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    disk_id, mac_dir = prepare()
    print(f"disk {disk_id} at {mac_dir}", flush=True)
    current: list[str] = []
    failed = 0
    tsv = STATE / "upload-bench.tsv"
    for variant in args.variant or VARIANTS:
        set_variant(variant, disk_id, current)
        for profile in args.profile or list(PROFILES):
            r = one(variant, profile, disk_id, mac_dir, args.size_mb)
            failed += not r["pass"]
            bad = [k for k, ok in r["checks"].items() if not ok]
            print(f"{'PASS' if r['pass'] else 'FAIL'}  {variant:8} {profile:8} {r['mb']:5} MB in {r['seconds']:6.1f} s"
                  f"  {r['mbps']:6.1f} Mbit/s" + (f" ({r['line_use']:.0%} of the line)" if r["line_use"] else "")
                  + f"  rtt p50 {r['rtt_p50_ms']} p95 {r['rtt_p95_ms']} max {r['rtt_max_ms']} ms"
                  f"  rss {r['rss_max_mb']} MB free {r['free_min_mb']} MB  shaper {r['shaper_mbps']}  commits {r['commit_errors']}"
                  + (f"  FAILED: {', '.join(bad)}" if bad else "") + (f"  ({r['error']})" if r["error"] else ""),
                  flush=True)
            new = not tsv.exists()
            with open(tsv, "a") as f:
                keys = [k for k in r if k != "checks"]
                if new:
                    f.write("time\t" + "\t".join(keys) + "\n")
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\t" + "\t".join(str(r[k]) for k in keys) + "\n")
    set_variant("base", disk_id, current)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
