#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.55.0", "boto3==1.43.114"]
# ///
"""
The team version end to end on this Mac: the local stack (deploy/local/stack.sh), a real Lima
VM, the disks in the Finder, the app's Core and Account, the admin web in Chrome.

  1. the admin signs in to the web and creates the disks: managed (STACK=managed, the
     default), two disks with Garage buckets; manual (STACK=manual), one disk on the real
     bucket and key of E2E_STORAGE_ENV, probed at creation and formatted there; each disk
     with its own Valkey instance on the meta service;
  2. the admin invites a member in the web with write on disk A and read on disk B (managed),
     and the member accepts the invitation;
  3. the member signs in through the app's real Core against the real engine (the ignored
     Rust test account::e2e_engine), which applies: <share>/<A> writable, <share>/<B>
     read-only;
  4. a file written on A from the Mac reads back identical, cold reads after the cache is
     freed (in the VM, then from the Mac), and a second device (the admin's, registered
     through the device API, mounted in a container) reads the same bytes;
  5. manual: the bucket holds the volume under its own prefix, the mount signs with the
     disk's region (no signature or region error in its log), and the disk's key reaches
     nothing on the Mac (disks file, engine home, argv) nor any argv in the VM;
  6. the admin lowers A to read in the web: after the apply a write on A fails;
  7. the admin revokes the member's device in the web: the disks are cleared, nothing is left
     in the VM, the share is unmounted; any other install's share is still mounted.

Each step prints PASS or FAIL with its time. The engine is the one installed in FLOPPY_HOME
(default /tmp/fe2e, a short path: Lima's sockets must fit in 104 characters) with the VM
FLOPPY_VM (default fe2e); it refuses the real ~/.floppy. Install it once from
scripts/build-engine.sh's output: FLOPPY_HOME=/tmp/fe2e FLOPPY_VM=fe2e bash <out>/install.sh.
The engine finds its own share by its VM's address, so it runs beside a real install whose
disk is mounted: its share then lands on /Volumes/Floppy-1, and every path here is read from
the engine's `mount_point`.

Usage: deploy/local/e2e.py                    (needs the stack up, Google Chrome, uv, Rust)
       STACK=manual E2E_STORAGE_ENV=<file> deploy/local/e2e.py

E2E_STORAGE_ENV is a file with BUCKET_URL (<endpoint>/<bucket>), ACCESS_KEY and SECRET_KEY, a
tester's config.env for instance; the disk's region is the organization's (stack.sh's
LOCAL_S3_REGION). The run creates objects only under the new disk's prefix (its JuiceFS
name), plus the probe's own object, written and removed at creation; it deletes nothing.
E2E_FILE_MB sets the test file's size (default 8, manual 48).

Secrets (the device's password, the S3 keys) stay in memory and reach the container through
the environment of docker exec, never argv or a file; nothing secret is printed.
"""

import base64
import errno
import fcntl
import hashlib
import json
import os
import queue
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
STACK = os.environ.get("STACK", "managed")
if STACK not in ("managed", "manual"):
    sys.exit("STACK is managed (the default) or manual")
MANUAL = STACK == "manual"
# As stack.sh: the manual stack's project, state and port.
PROJECT = "floppy-local-manual" if MANUAL else "floppy-local"
STATE = HERE / (".state-manual" if MANUAL else ".state")
SERVER = "http://127.0.0.1:4593" if MANUAL else "http://127.0.0.1:4583"
API = f"{SERVER}/api/v1"
ADMIN = "admin@floppy.test"
CONTAINER = f"{PROJECT}-server-1"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HOME = os.environ.get("FLOPPY_HOME", "/tmp/fe2e")
VM = os.environ.get("FLOPPY_VM", "fe2e")
ENGINE = f"{HOME}/bin/floppy"
LIMACTL = f"{HOME}/lima/bin/limactl"
FILE_MB = int(os.environ.get("E2E_FILE_MB", "48" if MANUAL else "8"))
# JuiceFS or S3 refusing the mount's signature or region.
SIGNING_ERRORS = re.compile(
    r"AuthorizationHeaderMalformed|SignatureDoesNotMatch|InvalidRegion|IllegalLocationConstraint"
    r"|InvalidAccessKeyId|AccessDenied"
)
RESULTS: list[tuple[str, bool, float, str]] = []
SECRETS: list[str] = []


class Failed(Exception):
    """A step that failed; the ones after it depend on it."""


def masked(text: str) -> str:
    for s in SECRETS:
        text = text.replace(s, "****")
    return text


def record(name: str, ok: bool, seconds: float, note: str = "") -> None:
    note = masked(note)
    RESULTS.append((name, ok, seconds, note))
    print(f"{'PASS' if ok else 'FAIL'}  {name} ({seconds:.1f} s){'  ' + note if note else ''}", flush=True)
    if not ok:
        raise Failed(name)


def run(*args: str, env: dict[str, str] | None = None, check: bool = True, timeout: float = 600,
        input: str | None = None) -> subprocess.CompletedProcess[str]:
    """A command; its output is returned, never printed (it may hold secrets)."""
    result = subprocess.run(args, capture_output=True, text=True, env={**os.environ, **(env or {})},
                            timeout=timeout, input=input)
    if check and result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-8:]
        raise RuntimeError(f"{' '.join(args[:4])} failed: " + " | ".join(tail))
    return result


def dc(*args: str, **kw) -> subprocess.CompletedProcess[str]:
    return run("docker", "compose", "-p", PROJECT, "--env-file", str(STATE / "local.env"),
               "-f", str(ROOT / "deploy/docker-compose.yml"), "-f", str(HERE / "compose.yml"), *args, **kw)


def wait(what: str, predicate, limit: float, every: float = 1.0):
    """Polls until `predicate` returns something truthy; that value, or Failed after `limit`."""
    start = time.monotonic()
    last_error = ""
    while time.monotonic() - start < limit:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            last_error = str(e)[:200]
        time.sleep(every)
    raise Failed(f"{what}: not within {limit:.0f} s {last_error}")


def storage() -> dict[str, str]:
    """The manual disk's endpoint, bucket and key, from E2E_STORAGE_ENV; the key is masked."""
    path = os.environ.get("E2E_STORAGE_ENV")
    if not path:
        sys.exit("STACK=manual needs E2E_STORAGE_ENV: a file with BUCKET_URL, ACCESS_KEY, SECRET_KEY")
    values: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    missing = [k for k in ("BUCKET_URL", "ACCESS_KEY", "SECRET_KEY") if not values.get(k)]
    if missing:
        sys.exit(f"E2E_STORAGE_ENV lacks {', '.join(missing)}")
    endpoint, bucket = values["BUCKET_URL"].rstrip("/").rsplit("/", 1)
    SECRETS.extend([values["SECRET_KEY"], values["ACCESS_KEY"], bucket])
    return {"endpoint": endpoint, "bucket": bucket, "accessKeyId": values["ACCESS_KEY"],
            "secretKey": values["SECRET_KEY"]}


def s3_client(store: dict[str, str], region: str):
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=store["endpoint"], region_name=region or "us-east-1",
                        aws_access_key_id=store["accessKeyId"], aws_secret_access_key=store["secretKey"],
                        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 2}))


# Mail from the server's log (console backend).

def server_log() -> str:
    out = run("docker", "logs", CONTAINER, check=False)
    return out.stdout + out.stderr


def mails_to(to: str) -> int:
    return server_log().count(f"To: {to}\n")


def mail_to(to: str, after: int) -> str:
    def newest():
        log = server_log()
        if log.count(f"To: {to}\n") > after:
            return "\n".join(log[log.rfind(f"To: {to}\n"):].splitlines()[:16])
        return None
    return wait(f"a mail to {to}", newest, 20, 0.3)


def token_in(mail: str) -> str:
    return urllib.parse.unquote(re.search(r"token=(\S+)", mail).group(1))


def code_in(mail: str) -> str:
    return re.search(r"(?<!\d)(\d{6})(?!\d)", mail).group(1)


# The engine and the VM, as the app sees them.

def engine_env() -> dict[str, str]:
    return {"FLOPPY_HOME": HOME, "FLOPPY_VM": VM}


def engine_status() -> dict:
    return json.loads(run(ENGINE, "status", "--json", env=engine_env()).stdout)


def disk_of(status: dict, disk_id: str) -> dict:
    return next((d for d in status.get("disks") or [] if d["id"] == disk_id), {})


def in_vm(script: str, timeout: float = 600) -> str:
    return run(LIMACTL, "shell", "--workdir", "/", VM, "sudo", "sh", "-c", script,
               env={"LIMA_HOME": f"{HOME}/vms"}, timeout=timeout).stdout


def smb_shares() -> set[str]:
    """Every Floppy share on this Mac, as `/sbin/mount` lists it."""
    return {line for line in run("/sbin/mount").stdout.splitlines() if re.match(r"^//drive@[^/]+/Floppy on ", line)}


def mounted_at(path: str | None) -> bool:
    return bool(path) and any(f" on {path} (smbfs" in line for line in smb_shares())


def write_fails(path: str) -> str | None:
    """The errno name of a failed write at `path`, or None when the write went through."""
    try:
        with open(path, "wb") as f:
            f.write(b"x" * 4096)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))
    os.remove(path)
    return None


def read_uncached(path: str) -> str:
    """sha256 of a file read with the Mac's cache off (F_NOCACHE), so the bytes come from the VM."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        fcntl.fcntl(f.fileno(), fcntl.F_NOCACHE, 1)
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def cold(path: str) -> None:
    """The file's blocks out of the VM's cache and its page cache: the next read is from S3."""
    run(ENGINE, "free", path, env=engine_env())
    in_vm("sync; echo 3 > /proc/sys/vm/drop_caches")


def mbps(size: int, seconds: float) -> str:
    return f"{size / 1e6 / seconds:.1f} MB/s"


# The Rust device: the app's Account and Core on the real engine.

class Device:
    def __init__(self, email: str):
        self.events: queue.Queue[dict] = queue.Queue()
        self.latest: dict[str, object] = {}
        self.log = open(STATE / "e2e-device.log", "w")
        env = {**os.environ, "FLOPPY_HOME": HOME, "FLOPPY_VM": VM, "FLOPPY_E2E_SERVER": SERVER,
               "FLOPPY_E2E_CONTAINER": CONTAINER, "FLOPPY_E2E_EMAIL": email}
        self.proc = subprocess.Popen(
            ["cargo", "test", "--quiet", "account::e2e_engine::e2e_engine_device_against_the_local_stack",
             "--", "--ignored", "--nocapture", "--exact"],
            cwd=ROOT / "app", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.log.write(line)
            self.log.flush()
            if line.startswith("E2E "):
                event = json.loads(line[4:])
                self.latest[event["event"]] = event["data"]
                self.events.put(event)

    def wait(self, name: str, limit: float) -> object:
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if name in self.latest:
                return self.latest[name]
            if self.proc.poll() is not None and self.events.empty():
                time.sleep(0.5)
                if name in self.latest:
                    return self.latest[name]
                raise Failed(f"the device test exited ({self.proc.returncode}) before {name}: see {self.log.name}")
            time.sleep(0.2)
        raise Failed(f"no {name} from the device within {limit:.0f} s")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# The admin's calls with the web session.

class Web:
    def __init__(self, page: Page):
        self.page = page

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        r = self.page.request.fetch(f"{API}{path}", method=method, data=body,
                                    headers={"Origin": SERVER, "Content-Type": "application/json"})
        try:
            return r.status, r.json()
        except Exception:  # an empty or non-JSON body
            return r.status, None


def open_menu(page: Page, trigger) -> None:
    """Opens a menu: scrolled into view first, since the web's menus close on any scroll and a
    visible browser delivers the scroll of a click's own scrolling after the click."""
    trigger.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    trigger.click()


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def http(method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    import urllib.request
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "Accept-Language": "it",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def second_device(email: str) -> dict:
    """A device registered for `email` through the device API, as the app does it."""
    verifier, challenge = pkce()
    before = mails_to(email)
    status, _ = http("POST", "/auth/app/sign-in", {"email": email, "codeChallenge": challenge,
                                                  "redirectUri": "http://127.0.0.1:9/callback",
                                                  "deviceName": "Seconda macchina e2e"})
    assert status == 200, status
    code = code_in(mail_to(email, before))
    status, grant = http("POST", "/auth/app/token", {"codeVerifier": verifier, "code": code})
    assert status == 200, status
    token = grant["deviceToken"]
    SECRETS.append(token)
    status, reg = http("POST", "/device/register", {"appVersion": "0.1.0", "macosVersion": "15.5"}, token)
    assert status in (200, 201), status
    SECRETS.append(reg["valkeyPassword"])
    if reg.get("s3"):
        SECRETS.append(reg["s3"]["secretKey"])
    return {"id": grant["deviceId"], "token": token, **reg}


def key_on_the_mac(store: dict[str, str], device: "Device") -> list[str]:
    """Where the manual disk's key shows up on the Mac: files of the engine's home (the VM's
    own disk image aside), the device test's log, the argv of every process."""
    found = []
    needles = [store["secretKey"].encode(), store["accessKeyId"].encode()]
    for path in Path(HOME).rglob("*"):
        if "vms" in path.relative_to(HOME).parts[:1] or not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if any(n in data for n in needles):
            found.append(str(path))
    if any(n in Path(device.log.name).read_bytes() for n in needles):
        found.append(device.log.name)
    argv = run("/bin/ps", "-axww", "-o", "args=").stdout
    if any(n.decode() in argv for n in needles):
        found.append("argv of a Mac process")
    return found


def main() -> int:
    if Path(HOME).resolve() == (Path.home() / ".floppy").resolve():
        sys.exit("FLOPPY_HOME is the real install: this test runs only on a development home")
    if not os.access(ENGINE, os.X_OK):
        sys.exit(f"no engine in {HOME}: install it first (see the docstring)")
    store = storage() if MANUAL else None
    STATE.mkdir(exist_ok=True)
    tag = secrets.token_hex(2).upper()
    # Manual: one disk, since each disk has its own bucket and key; its JuiceFS name (the
    # objects' prefix) starts with "ovh-", apart from a tester's floppy-* volumes.
    disk_a, disk_b = (f"OVH Progetti {tag}", None) if MANUAL else (f"Progetti {tag}", f"Archivio {tag}")
    disks = [d for d in (disk_a, disk_b) if d]
    member = f"membro-{tag.lower()}@floppy.test"
    device: Device | None = None
    second: dict | None = None
    others = smb_shares()  # other installs' shares: they must outlive the run
    t0 = time.monotonic()
    with sync_playwright() as p:
        # A visible window unless E2E_HEADLESS=1.
        browser = p.chromium.launch(executable_path=CHROME, headless=os.environ.get("E2E_HEADLESS") == "1")
        admin_page = browser.new_context(base_url=SERVER, locale="it-IT").new_page()
        member_page = browser.new_context(base_url=SERVER, locale="it-IT").new_page()
        web = Web(admin_page)
        try:
            t = time.monotonic()
            status, _ = http("GET", "/healthz")
            record(f"s01 local stack ({STACK}) answers on {SERVER}", status == 200, time.monotonic() - t)
            if engine_status().get("mount_point"):
                raise Failed(f"the engine in {HOME} already has its share mounted: a run left it behind")

            # 1. The admin in the web, the disks.
            t = time.monotonic()
            before = mails_to(ADMIN)
            http("POST", "/auth/sign-in", {"email": ADMIN})
            admin_page.goto(f"/accedi/link?token={urllib.parse.quote(token_in(mail_to(ADMIN, before)))}")
            admin_page.wait_for_url(re.compile(r"/$|/panoramica"), timeout=20_000)
            ids = {}
            for name in disks:
                body: dict[str, object] = {"name": name}
                if store is not None:
                    body["storage"] = {"endpoint": store["endpoint"], "bucket": store["bucket"],
                                       "accessKeyId": store["accessKeyId"], "secretKey": store["secretKey"]}
                status, created = web.call("POST", "/disks", body)
                if status != 201:
                    record(f"s02 create disk {name}", False, time.monotonic() - t, f"HTTP {status} {created}")
                ids[name] = created["id"]
            created_in = time.monotonic() - t

            def ready():
                _, listed = web.call("GET", "/disks")
                states = {d["name"]: d for d in listed if d["name"] in ids}
                if any(d["state"] == "failed" for d in states.values()):
                    raise Failed(f"a disk failed: { {n: d.get('error') for n, d in states.items()} }")
                return all(d["state"] == "ready" for d in states.values()) and len(states) == len(disks) and states
            listed = wait("the disks ready", ready, 120)
            note = f"created in {created_in:.1f} s"
            if MANUAL:
                _, health = web.call("GET", "/health")
                probe = next((d["storage"] for d in health["disks"] if d.get("id") == ids[disk_a]), {})  # type: ignore[index]
                region = listed[disk_a]["region"]
                note += (f", region {region}, probe {probe.get('state')} at {probe.get('lastProbeAt')}, "
                         f"versioning {probe.get('versioning', {}).get('state')}")
                ok = probe.get("state") == "ok" and bool(region)
            else:
                ok = True
            record(f"s02 admin signs in to the web, creates {', '.join(repr(d) for d in disks)} ({STACK})", ok,
                   time.monotonic() - t, note)
            t = time.monotonic()
            out = run(str(HERE / "stack.sh"), "hosts", env={**engine_env(), "STACK": STACK}).stdout.strip()
            record("s03 the VM resolves the disks' names to the Mac", True, time.monotonic() - t, out)

            # 2. The invitation, in the web.
            t = time.monotonic()
            before = mails_to(member)
            admin_page.goto("/persone")
            admin_page.get_by_role("button", name="Invita persona").first.click()
            dialog = admin_page.get_by_role("dialog")
            dialog.get_by_label("Email").fill(member)
            for name, level in ((disk_a, "Lettura e scrittura"), (disk_b, "Lettura")):
                if name is None:
                    continue
                open_menu(admin_page, dialog.get_by_role("button", name=re.compile(f"^Accesso a {re.escape(name)}:")))
                admin_page.get_by_role("menuitemradio", name=level, exact=True).click()
            dialog.get_by_role("button", name="Manda invito").click()
            admin_page.get_by_text(f"Invito mandato a {member}").wait_for(timeout=15_000)
            member_page.goto(f"/invito?token={urllib.parse.quote(token_in(mail_to(member, before)))}")
            member_page.wait_for_url(re.compile(r"/$|/panoramica|/dispositivi"), timeout=20_000)
            _, people = web.call("GET", "/people")
            person = next(x for x in people if x["email"] == member)
            _, access = web.call("GET", "/access")
            granted = {a["diskId"]: a["access"] for a in access if a["personId"] == person["id"]} if isinstance(access, list) else {}
            want = {ids[disk_a]: "write", **({ids[disk_b]: "read"} if disk_b else {})}
            record("s04 invited in the web with write on A" + (" and read on B" if disk_b else "") + ", invitation accepted",
                   person["status"] == "active" and all(granted.get(i) == a for i, a in want.items()),
                   time.monotonic() - t, f"status {person['status']}, granted {sorted(granted.values())}")

            # 3. The member's device: the app's Core on the real engine.
            t = time.monotonic()
            device = Device(member)
            device.wait("signed_in", 600)
            record("s05 the member signs in by code through the app's Account and Core", True, time.monotonic() - t)
            t = time.monotonic()

            def applied():
                s = engine_status()
                mp = s.get("mount_point")
                states = [disk_of(s, ids[n]) for n in disks]
                return (mp and all(d.get("state") == "mounted" for d in states)
                        and disk_of(s, ids[disk_a]).get("mounted_access") == "write"
                        and (not disk_b or disk_of(s, ids[disk_b]).get("mounted_access") == "read")
                        and all(os.path.isdir(f"{mp}/{n}") for n in disks) and s)
            s = wait("the disks mounted", applied, 300, 2)
            mount = s["mount_point"]
            modes = {d["folder"]: f"{d['mounted_access']}/{d['mode']}/{d['replica']}" for d in s["disks"]}
            beside = f", beside {len(others)} other share(s)" if others else ""
            record(f"s06 the engine applies: {', '.join(f'{mount}/{n}' for n in disks)} in the Finder", True,
                   time.monotonic() - t, f"access/metadata/replica {modes}{beside}")

            t = time.monotonic()
            size = FILE_MB * 1024 * 1024
            blob = secrets.token_bytes(size)
            digest = hashlib.sha256(blob).hexdigest()
            name_a = f"e2e-{tag}.bin"
            path_a = f"{mount}/{disk_a}/{name_a}"
            w = time.monotonic()
            with open(path_a, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            wrote = time.monotonic() - w
            with open(path_a, "rb") as f:
                back = hashlib.sha256(f.read()).hexdigest()
            record("s07 a file written on A from the Mac reads back identical", back == digest, time.monotonic() - t,
                   f"{FILE_MB} MB, sha256 {digest[:12]}, write+fsync {mbps(size, wrote)}")

            # Cold reads: in the VM straight from the mount, then from the Mac without its cache.
            t = time.monotonic()
            cold(path_a)
            vm_file = shlex.quote(f"/mnt/drive/{disk_a}/{name_a}")
            out = in_vm(f"a=$(date +%s.%N); h=$(sha256sum {vm_file} | cut -d' ' -f1); b=$(date +%s.%N); "
                        "echo \"$h $a $b\"").split()
            vm_seconds = float(out[2]) - float(out[1])
            cold(path_a)
            r = time.monotonic()
            mac_back = read_uncached(path_a)
            mac_seconds = time.monotonic() - r
            record("s07b cold reads after freeing the cache come back identical", out[0] == digest and mac_back == digest,
                   time.monotonic() - t, f"in the VM {mbps(size, vm_seconds)}, from the Mac (F_NOCACHE) {mbps(size, mac_seconds)}")
            if disk_b:
                t = time.monotonic()
                err = write_fails(f"{mount}/{disk_b}/e2e-{tag}.bin")
                record("s08 a write on B (read-only) fails with EROFS", err == "EROFS", time.monotonic() - t,
                       f"errno {err or 'none: the write went through'}")

            # 4. A second device reads the same bytes.
            t = time.monotonic()
            second = second_device(ADMIN)
            config_a = next(d for d in second["disks"] if d["id"] == ids[disk_a])
            meta = re.match(r"rediss://([^:/]+):(\d+)/(\d+)$", config_a["metaUrl"])
            dc("--profile", "device", "up", "-d", "--build", "--wait", "device", timeout=1800)
            env = {"E2E_USER": second["valkeyUsername"], "META_PASSWORD": second["valkeyPassword"],
                   "AWS_REGION": config_a.get("region") or "us-east-1"}
            if second.get("s3"):
                # Managed: the device's own key; manual: the key is in the disk's format.
                env |= {"AWS_ACCESS_KEY_ID": second["s3"]["accessKeyId"],
                        "AWS_SECRET_ACCESS_KEY": second["s3"]["secretKey"]}
            # The disk's name to the Mac (Docker's host gateway, IPv4: the published ports are
            # on 127.0.0.1), then a read-only mount through Traefik by name, as a VM does.
            script = (
                f"set -e; ip=$(getent ahostsv4 host.docker.internal | awk 'NR == 1 {{print $1}}'); "
                f"grep -q ' {meta.group(1)}$' /etc/hosts || echo \"$ip {meta.group(1)}\" >> /etc/hosts; "
                f"mkdir -p /mnt/{tag}; mountpoint -q /mnt/{tag} || juicefs mount "
                f"\"rediss://$E2E_USER@{meta.group(1)}:{meta.group(2)}/{meta.group(3)}?tls-ca-cert-file=/certs/ca.crt\" "
                f"/mnt/{tag} --read-only --no-usage-report -d --log /tmp/jfs-{tag}.log >/tmp/mount-{tag}.log 2>&1 "
                f"|| {{ tail -3 /tmp/mount-{tag}.log; exit 1; }}; "
                f"sha256sum '/mnt/{tag}/{name_a}' | cut -d' ' -f1")
            flags = [x for k in env for x in ("-e", k)]
            out = dc("exec", "-T", *flags, "device", "sh", "-c", script, env=env, check=False, timeout=300)
            seen = out.stdout.strip().splitlines()[-1:] if out.returncode == 0 else []
            record("s09 a second device (the admin's, registered through the device API) reads the same bytes",
                   seen == [digest], time.monotonic() - t,
                   f"through {meta.group(1).split('.')[0]}.meta:{meta.group(2)}, region {env['AWS_REGION']}"
                   if seen == [digest] else f"exit {out.returncode}: {(out.stdout + out.stderr)[-300:]}")

            if store is not None:
                # 5. Manual: the format on the bucket, the signature, where the key went.
                t = time.monotonic()
                volume = config_a["volumeName"]
                client = s3_client(store, config_a.get("region") or "")
                client.head_object(Bucket=store["bucket"], Key=f"{volume}/juicefs_uuid")
                chunks = client.list_objects_v2(Bucket=store["bucket"], Prefix=f"{volume}/chunks/", MaxKeys=1000)
                record("s10a the volume is on the bucket under its own prefix", chunks.get("KeyCount", 0) > 0,
                       time.monotonic() - t, f"prefix {volume}/, {chunks.get('KeyCount')} chunk objects")
                t = time.monotonic()
                unit = f"floppy-disk@{ids[disk_a]}"
                pid = in_vm(f"systemctl show -p MainPID --value {unit}").strip()
                region_env = in_vm(f"tr '\\0' '\\n' < /proc/{pid}/environ | grep '^AWS_REGION=' || true").strip()
                journal = in_vm(f"journalctl --no-pager -u {unit} -u 'floppy-disk@*'; true", timeout=120)
                bad = sorted({m.group(0) for m in SIGNING_ERRORS.finditer(journal)})
                container_log = dc("exec", "-T", "device", "sh", "-c", f"cat /tmp/jfs-{tag}.log", check=False).stdout
                bad += sorted({m.group(0) for m in SIGNING_ERRORS.finditer(container_log)})
                record("s10b the mounts sign with the disk's region: no signature or region error",
                       region_env == f"AWS_REGION={config_a.get('region')}" and not bad, time.monotonic() - t,
                       f"VM mount {region_env or 'no AWS_REGION'}, errors {bad or 'none'} "
                       f"in {len(journal.splitlines())} journal lines and the second device's log")
                t = time.monotonic()
                where = key_on_the_mac(store, device)
                vm_argv = in_vm("cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\\0' ' '; true", timeout=120)
                if store["secretKey"] in vm_argv or store["accessKeyId"] in vm_argv:
                    where.append("argv of a VM process")
                file_ok = Path(HOME, "disks.json").is_file()
                record("s10c the disk's key reaches nothing on the Mac in clear, nor any argv in the VM",
                       file_ok and not where, time.monotonic() - t,
                       f"checked disks.json, {HOME} (the VM image aside), the device log, Mac and VM argv: "
                       f"{where or 'not found'}")

            # 6. A lowered to read, in the web.
            t = time.monotonic()
            name = person["name"] or member
            admin_page.goto("/accessi")
            # The member's row, by its link: earlier runs' members may have the same name.
            row = admin_page.locator("tr", has=admin_page.locator(f'a[href="/persone/{person["id"]}"]'))
            open_menu(admin_page, row.get_by_role("button", name=re.compile(
                f"^Accesso di {re.escape(name)} a {re.escape(disk_a)}:")))
            admin_page.get_by_role("menuitemradio", name="Lettura", exact=True).click()

            def lowered():
                a = disk_of(engine_status(), ids[disk_a])
                return a.get("state") == "mounted" and a.get("mounted_access") == "read" and not a.get("pending")
            wait("A remounted read-only", lowered, 300, 2)
            remounted = time.monotonic() - t
            err = write_fails(f"{mount}/{disk_a}/after-lower-{tag}.bin")
            with open(path_a, "rb") as f:
                still = hashlib.sha256(f.read()).hexdigest() == digest
            record("s11 A lowered to read in the web: after the apply a write on A fails",
                   err is not None and still, time.monotonic() - t,
                   f"remounted read-only after {remounted:.0f} s, errno {err or 'none'}, the file still reads")

            # 7. The member's device revoked, in the web.
            t = time.monotonic()
            user = device.latest.get("file", {}).get("meta_user")  # type: ignore[union-attr]
            admin_page.goto("/dispositivi")
            row = admin_page.locator("tr", has_text=str(user))
            row.get_by_role("button", name="Revoca").click()
            admin_page.get_by_role("dialog").get_by_role("button", name="Revoca il dispositivo").click()
            revoked = device.wait("revoked", 300)
            device.proc.wait(60)
            left = in_vm("ls -A /etc/floppy/disks /var/cache/floppy/disks /var/lib/floppy-replica /etc/floppy-replica "
                         "2>/dev/null | grep -v ':$' | grep -v '^tls\\.' | grep . ; ls -A /mnt/drive; "
                         "test -e /etc/floppy/team/secrets.json && echo secrets.json; "
                         "systemctl list-units --all --no-legend 'floppy-disk@*' 'floppy-replica@*'; true").split()
            kept = others <= smb_shares()
            ok = (device.proc.returncode == 0 and not mounted_at(mount) and not engine_status().get("mount_point")
                  and not left and not revoked["disks"] and kept)  # type: ignore[index]
            record("s12 the device revoked in the web: disks cleared, nothing left in the VM, the share unmounted",
                   ok, time.monotonic() - t,
                   f"device test exit {device.proc.returncode}, {mount} {'mounted' if mounted_at(mount) else 'unmounted'}, "
                   f"left in the VM: {left or 'nothing'}, other shares {'still mounted' if kept else 'GONE'}")
        except Failed as e:
            if not RESULTS or RESULTS[-1][1]:
                RESULTS.append((str(e), False, 0, ""))
                print(f"FAIL  {masked(str(e))}", flush=True)
        except Exception as e:  # a crashed step is a failed step, with its cause
            note = masked(f"{type(e).__name__}: {e}"[:500])
            RESULTS.append(("crash", False, 0, note))
            print(f"FAIL  crash: {note}", flush=True)
            admin_page.screenshot(path=str(STATE / "e2e-admin.png"))
        finally:
            if device is not None:
                device.stop()
                # A run that stopped halfway leaves no device behind: revoked on the server,
                # its disks cleared on this Mac.
                if "revoked" not in device.latest:
                    user = (device.latest.get("file") or {}).get("meta_user")  # type: ignore[union-attr]
                    _, devices = web.call("GET", "/devices")
                    for d in devices if isinstance(devices, list) else []:
                        if d.get("valkeyUser") == user and not d.get("revokedAt"):
                            web.call("POST", f"/devices/{d['id']}/revoke")
                    run(ENGINE, "disks", "clear", env=engine_env(), check=False)
            if second is not None:
                dc("exec", "-T", "device", "sh", "-c", f"mountpoint -q /mnt/{tag} && umount /mnt/{tag}; true", check=False)
                web.call("POST", f"/devices/{second['id']}/revoke")
            browser.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed, {time.monotonic() - t0:.0f} s in all")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
