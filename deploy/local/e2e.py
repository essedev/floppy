#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.55.0"]
# ///
"""
The team version end to end on this Mac: the local stack (deploy/local/stack.sh), a real Lima
VM, the disks in the Finder, the app's Core and Account, the admin web in Chrome.

  1. the admin signs in to the web and creates two disks (managed: Garage buckets, their
     own Valkey instances on the meta service);
  2. the admin invites a member in the web with write on disk A and read on disk B, and the
     member accepts the invitation;
  3. the member signs in through the app's real Core against the real engine (the ignored
     Rust test account::e2e_engine), which applies: /Volumes/Floppy/<A> writable,
     /Volumes/Floppy/<B> read-only;
  4. a file written on A from the Mac reads back identical, and a second device (the admin's,
     registered through the device API, mounted in a container) reads the same bytes;
  5. the admin lowers A to read in the web: after the apply a write on A fails;
  6. the admin revokes the member's device in the web: the disks are cleared, nothing is left
     in the VM, /Volumes/Floppy is unmounted.

Each step prints PASS or FAIL with its time. The engine is the one installed in FLOPPY_HOME
(default /tmp/fe2e, a short path: Lima's sockets must fit in 104 characters) with the VM
FLOPPY_VM (default fe2e); it refuses the real ~/.floppy. Install it once from
scripts/build-engine.sh's output: FLOPPY_HOME=/tmp/fe2e FLOPPY_VM=fe2e bash <out>/install.sh.

Usage: deploy/local/e2e.py   (needs the stack up, Google Chrome, uv, Rust)

Secrets (the device's password and S3 key) stay in memory and reach the container through
the environment of docker exec, never argv or a file; nothing secret is printed.
"""

import base64
import errno
import hashlib
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
STATE = HERE / ".state"
SERVER = "http://127.0.0.1:4583"
API = f"{SERVER}/api/v1"
ADMIN = "admin@floppy.test"
CONTAINER = "floppy-local-server-1"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MOUNT = "/Volumes/Floppy"
HOME = os.environ.get("FLOPPY_HOME", "/tmp/fe2e")
VM = os.environ.get("FLOPPY_VM", "fe2e")
ENGINE = f"{HOME}/bin/floppy"
LIMACTL = f"{HOME}/lima/bin/limactl"
RESULTS: list[tuple[str, bool, float, str]] = []
SECRETS: list[str] = []


class Failed(Exception):
    """A step that failed; the ones after it depend on it."""


def record(name: str, ok: bool, seconds: float, note: str = "") -> None:
    for s in SECRETS:
        note = note.replace(s, "****")
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
    return run("docker", "compose", "-p", "floppy-local", "--env-file", str(STATE / "local.env"),
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

def engine_status() -> dict:
    return json.loads(run(ENGINE, "status", "--json", env={"FLOPPY_HOME": HOME, "FLOPPY_VM": VM}).stdout)


def disk_of(status: dict, disk_id: str) -> dict:
    return next((d for d in status.get("disks") or [] if d["id"] == disk_id), {})


def in_vm(script: str) -> str:
    return run(LIMACTL, "shell", "--workdir", "/", VM, "sudo", "sh", "-c", script,
               env={"LIMA_HOME": f"{HOME}/vms"}).stdout


def smb_mounted() -> bool:
    return any(f" on {MOUNT} (smbfs" in line for line in run("/sbin/mount").stdout.splitlines())


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


def main() -> int:
    if Path(HOME).resolve() == (Path.home() / ".floppy").resolve():
        sys.exit("FLOPPY_HOME is the real install: this test runs only on a development home")
    if not os.access(ENGINE, os.X_OK):
        sys.exit(f"no engine in {HOME}: install it first (see the docstring)")
    STATE.mkdir(exist_ok=True)
    tag = secrets.token_hex(2).upper()
    disk_a, disk_b = f"Progetti {tag}", f"Archivio {tag}"
    member = f"membro-{tag.lower()}@floppy.test"
    device: Device | None = None
    second: dict | None = None
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
            record("s01 local stack answers on 127.0.0.1:4583", status == 200, time.monotonic() - t)
            if smb_mounted():
                raise Failed(f"{MOUNT} is already mounted: another install is using it")

            # 1. The admin in the web, two disks.
            t = time.monotonic()
            before = mails_to(ADMIN)
            http("POST", "/auth/sign-in", {"email": ADMIN})
            admin_page.goto(f"/accedi/link?token={urllib.parse.quote(token_in(mail_to(ADMIN, before)))}")
            admin_page.wait_for_url(re.compile(r"/$|/panoramica"), timeout=20_000)
            ids = {}
            for name in (disk_a, disk_b):
                status, body = web.call("POST", "/disks", {"name": name})
                if status != 201:
                    record(f"s02 create disk {name}", False, time.monotonic() - t, f"HTTP {status} {body}")
                ids[name] = body["id"]

            def ready():
                _, disks = web.call("GET", "/disks")
                states = {d["name"]: d["state"] for d in disks if d["name"] in ids}
                if any(s == "failed" for s in states.values()):
                    raise Failed(f"a disk failed: {states}")
                return all(s == "ready" for s in states.values()) and len(states) == 2
            wait("both disks ready", ready, 60)
            record(f"s02 admin signs in to the web, creates '{disk_a}' and '{disk_b}' (managed)", True,
                   time.monotonic() - t)
            t = time.monotonic()
            out = run(str(HERE / "stack.sh"), "hosts", env={"FLOPPY_HOME": HOME, "FLOPPY_VM": VM}).stdout.strip()
            record("s03 the VM resolves the disks' names to the Mac", True, time.monotonic() - t, out)

            # 2. The invitation, in the web.
            t = time.monotonic()
            before = mails_to(member)
            admin_page.goto("/persone")
            admin_page.get_by_role("button", name="Invita persona").first.click()
            dialog = admin_page.get_by_role("dialog")
            dialog.get_by_label("Email").fill(member)
            for name, level in ((disk_a, "Lettura e scrittura"), (disk_b, "Lettura")):
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
            record("s04 invited in the web with write on A and read on B, invitation accepted",
                   person["status"] == "active" and granted.get(ids[disk_a]) == "write" and granted.get(ids[disk_b]) == "read",
                   time.monotonic() - t, f"status {person['status']}, A {granted.get(ids[disk_a])}, B {granted.get(ids[disk_b])}")

            # 3. The member's device: the app's Core on the real engine.
            t = time.monotonic()
            device = Device(member)
            device.wait("signed_in", 600)
            record("s05 the member signs in by code through the app's Account and Core", True, time.monotonic() - t)
            t = time.monotonic()

            def applied():
                s = engine_status()
                a, b = disk_of(s, ids[disk_a]), disk_of(s, ids[disk_b])
                return (s.get("mount_point") == MOUNT and a.get("state") == "mounted" and b.get("state") == "mounted"
                        and a.get("mounted_access") == "write" and b.get("mounted_access") == "read"
                        and os.path.isdir(f"{MOUNT}/{disk_a}") and os.path.isdir(f"{MOUNT}/{disk_b}") and s)
            s = wait("the disks mounted", applied, 300, 2)
            modes = {d["folder"]: f"{d['mounted_access']}/{d['mode']}/{d['replica']}" for d in s["disks"]}
            record(f"s06 the engine applies: {MOUNT}/{disk_a} and {MOUNT}/{disk_b} in the Finder", True,
                   time.monotonic() - t, f"access/metadata/replica {modes}")

            t = time.monotonic()
            blob = secrets.token_bytes(8 * 1024 * 1024)
            digest = hashlib.sha256(blob).hexdigest()
            path_a = f"{MOUNT}/{disk_a}/e2e-{tag}.bin"
            with open(path_a, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            with open(path_a, "rb") as f:
                back = hashlib.sha256(f.read()).hexdigest()
            record("s07 a file written on A from the Mac reads back identical", back == digest, time.monotonic() - t,
                   f"8 MB, sha256 {digest[:12]}")
            t = time.monotonic()
            err = write_fails(f"{MOUNT}/{disk_b}/e2e-{tag}.bin")
            record("s08 a write on B (read-only) fails with EROFS", err == "EROFS", time.monotonic() - t,
                   f"errno {err or 'none: the write went through'}")

            # 4. A second device reads the same bytes.
            t = time.monotonic()
            second = second_device(ADMIN)
            config_a = next(d for d in second["disks"] if d["id"] == ids[disk_a])
            meta = re.match(r"rediss://([^:/]+):(\d+)/(\d+)$", config_a["metaUrl"])
            dc("--profile", "device", "up", "-d", "--build", "--wait", "device", timeout=1800)
            env = {"E2E_USER": second["valkeyUsername"], "META_PASSWORD": second["valkeyPassword"],
                   "AWS_ACCESS_KEY_ID": second["s3"]["accessKeyId"], "AWS_SECRET_ACCESS_KEY": second["s3"]["secretKey"],
                   "AWS_REGION": config_a.get("region") or "us-east-1"}
            # The disk's name to the Mac (Docker's host gateway, IPv4: the published ports are
            # on 127.0.0.1), then a read-only mount through Traefik by name, as a VM does.
            script = (
                f"set -e; ip=$(getent ahostsv4 host.docker.internal | awk 'NR == 1 {{print $1}}'); "
                f"grep -q ' {meta.group(1)}$' /etc/hosts || echo \"$ip {meta.group(1)}\" >> /etc/hosts; "
                f"mkdir -p /mnt/{tag}; mountpoint -q /mnt/{tag} || juicefs mount "
                f"\"rediss://$E2E_USER@{meta.group(1)}:{meta.group(2)}/{meta.group(3)}?tls-ca-cert-file=/certs/ca.crt\" "
                f"/mnt/{tag} --read-only --no-usage-report -d --log /tmp/jfs-{tag}.log >/tmp/mount-{tag}.log 2>&1 "
                f"|| {{ tail -3 /tmp/mount-{tag}.log; exit 1; }}; "
                f"sha256sum '/mnt/{tag}/e2e-{tag}.bin' | cut -d' ' -f1")
            flags = [x for k in env for x in ("-e", k)]
            out = dc("exec", "-T", *flags, "device", "sh", "-c", script, env=env, check=False, timeout=300)
            seen = out.stdout.strip().splitlines()[-1:] if out.returncode == 0 else []
            record("s09 a second device (the admin's, registered through the device API) reads the same bytes",
                   seen == [digest], time.monotonic() - t,
                   f"through {meta.group(1).split('.')[0]}.meta:{meta.group(2)}" if seen == [digest]
                   else f"exit {out.returncode}: {(out.stdout + out.stderr)[-300:]}")

            # 5. A lowered to read, in the web.
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
            err = write_fails(f"{MOUNT}/{disk_a}/after-lower-{tag}.bin")
            with open(path_a, "rb") as f:
                still = hashlib.sha256(f.read()).hexdigest() == digest
            record("s10 A lowered to read in the web: after the apply a write on A fails",
                   err is not None and still, time.monotonic() - t,
                   f"remounted read-only after {remounted:.0f} s, errno {err or 'none'}, the file still reads")

            # 6. The member's device revoked, in the web.
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
            ok = device.proc.returncode == 0 and not smb_mounted() and not left and not revoked["disks"]  # type: ignore[index]
            record("s11 the device revoked in the web: disks cleared, nothing left in the VM, the share unmounted",
                   ok, time.monotonic() - t,
                   f"device test exit {device.proc.returncode}, {MOUNT} {'mounted' if smb_mounted() else 'unmounted'}, "
                   f"left in the VM: {left or 'nothing'}")
        except Failed as e:
            if not RESULTS or RESULTS[-1][1]:
                RESULTS.append((str(e), False, 0, ""))
                print(f"FAIL  {e}", flush=True)
        except Exception as e:  # a crashed step is a failed step, with its cause
            note = str(e)[:500]
            for s in SECRETS:
                note = note.replace(s, "****")
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
                    run(ENGINE, "disks", "clear", env={"FLOPPY_HOME": HOME, "FLOPPY_VM": VM}, check=False)
            if second is not None:
                dc("exec", "-T", "device", "sh", "-c", f"mountpoint -q /mnt/{tag} && umount /mnt/{tag}; true", check=False)
                web.call("POST", f"/devices/{second['id']}/revoke")
            browser.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed, {time.monotonic() - t0:.0f} s in all")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
