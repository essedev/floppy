/**
 * MOCK BACKEND, browser only. In the Vite dev server there is no Tauri, so the
 * views run on these invented states to be looked at and checked with
 * Playwright: `?view=panel|settings|welcome&scenario=<name>`. The account has
 * its own scenarios, `auth_<phase or error>` (signed in otherwise), and steps
 * through them on clicks: code 123456 signs in, any other is wrong. The team
 * version's are `team_<case>`. The app itself never uses this module. Folder
 * names come from concept-a-panel.
 */

import type { Backend } from "@/lib/backend";
import type { AuthView, DiskStatus, DiskView, Folder, Notice, SetupProgress, TeamView } from "@/lib/types";

const params = new URLSearchParams(globalThis.location?.search ?? "");
const scenario = params.get("scenario") ?? "connected";

const GB = 1024;
const base: DiskStatus = {
  state: "connected",
  problem: null,
  mount_point: "/Volumes/Floppy",
  wrong_mount_point: false,
  space: {
    cache_used_mb: 48.2 * GB,
    cache_limit_mb: 100 * GB,
    mac_free_mb: 214 * GB,
    reserve_mb: 20 * GB,
    low: false,
  },
  job: null,
  last_error: null,
  engine_error: null,
  next_check_s: 30,
  checked_ms: Date.now(),
  engine_path: "/Users/giulia/.floppy/bin/floppy",
  install: null,
  bundled: true,
  mode: "single",
  team: null,
};

const FOLDERS: Folder[] = [
  "Archivio 2025",
  "Cantine Arnaldi - Vendemmia",
  "Fondazione Lirica - Backstage",
  "Pastificio Moretti - Spot Natale",
  "Showreel 2026",
].map((name) => ({ name, path: `/Volumes/Floppy/${name}` }));

const unmounted = { mount_point: null, space: { ...base.space!, cache_used_mb: null, cache_limit_mb: null } };

/** A mounted read-write team disk; the scenarios change what they need. */
function disk(folder: string, over: Partial<DiskView> = {}): DiskView {
  return {
    id: folder.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
    folder,
    path: `/Volumes/Floppy/${folder}`,
    access: "write",
    enabled: true,
    state: "mounted",
    mounted_access: "write",
    pending: null,
    cache_used_mb: 3.2 * GB,
    cache_size_mb: 25 * GB,
    replica: "up",
    meta_mode: "replica",
    online: true,
    ...over,
  };
}
const off = { path: null, enabled: false, state: "disabled", mounted_access: null, cache_used_mb: null, replica: "stopped", meta_mode: null, online: null } as const;
const teamBase: TeamView = {
  disks: [
    disk("Archivio 2025", { access: "read", mounted_access: "read", cache_used_mb: 812 }),
    disk("Showreel 2026", { cache_used_mb: 12.4 * GB }),
    disk("Pastificio Moretti - Spot Natale", { cache_used_mb: 31.6 * GB }),
  ],
  rejected: [],
  applying: false,
  error: null,
  restart_needed: false,
  refused_single: false,
};
const team = (over: Partial<TeamView> = {}): Partial<DiskStatus> => ({ mode: "team", team: { ...teamBase, ...over } });

const SCENARIOS: Record<string, Partial<DiskStatus>> = {
  connected: {},
  activity: {
    job: { kind: "warmup", path: FOLDERS[1]!.path, name: FOLDERS[1]!.name, started_ms: Date.now() },
  },
  starting: { state: "starting", ...unmounted, job: { kind: "start", path: null, name: null, started_ms: 0 } },
  stopping: { state: "stopping", job: { kind: "stop", path: null, name: null, started_ms: 0 } },
  unreachable: { state: "unreachable", problem: "not_mounted", ...unmounted },
  engine_error: {
    state: "unreachable",
    problem: "engine",
    ...unmounted,
    engine_error: "the floppy command did not answer within 25 s",
  },
  off: { state: "off", ...unmounted },
  not_installed: { state: "not_installed", ...unmounted, space: null, install: "fresh" },
  not_configured: { state: "not_installed", ...unmounted, space: null },
  no_bundle: { state: "not_installed", ...unmounted, space: null, bundled: false },
  update: { install: "update" },
  needs_setup: { state: "needs_setup", ...unmounted },
  low_space: { space: { ...base.space!, mac_free_mb: 12 * GB, low: true } },
  wrong_mount: { mount_point: "/Volumes/Floppy-1", wrong_mount_point: true },
  job_error: {
    last_error: {
      kind: "warmup",
      name: "Showreel 2026",
      message: "the disk is not mounted",
    },
  },
  full_cache: { space: { ...base.space!, cache_used_mb: 97.4 * GB } },
  team_connected: team(),
  team_states: team({
    disks: [
      disk("Archivio 2025", { access: "read", mounted_access: "read", cache_used_mb: 812 }),
      disk("Showreel 2026", { access: "read", pending: "remount", cache_used_mb: 12.4 * GB }),
      disk("Cantine Arnaldi - Vendemmia", { online: false, meta_mode: "offline", cache_used_mb: 4.1 * GB }),
      disk("Fondazione Lirica - Backstage", { path: null, state: "failed", mounted_access: null, cache_used_mb: null }),
      disk("Pastificio Moretti - Spot Natale", { ...off }),
    ],
  }),
  team_starting: { state: "starting", ...unmounted, ...team({ applying: true, disks: teamBase.disks.map((d) => ({ ...d, path: null, state: null })) }) },
  team_restart: team({ restart_needed: true }),
  team_error: team({ error: "the floppy command failed: the VM is not responding, run: floppy status" }),
  team_no_disks: { state: "no_disks", ...unmounted, ...team({ disks: [] }) },
  team_all_off: {
    state: "no_disks",
    ...unmounted,
    ...team({ disks: teamBase.disks.map((d) => ({ ...d, ...off })) }),
  },
  team_single: { ...team({ refused_single: true }), mode: "single" },
};

let status: DiskStatus = { ...base, ...(SCENARIOS[scenario] ?? {}) };
let setup: SetupProgress = {
  running: scenario === "setup_running",
  step: scenario === "setup_running" ? 2 : 0,
  done: false,
  error:
    scenario === "setup_space"
      ? { kind: "space", message: "not enough free space on this Mac: 3 GB free, at least 30 GB needed" }
      : null,
};
if (scenario === "setup_running" || scenario === "setup_space") status = { ...status, state: "needs_setup" };

const statusListeners = new Set<(s: DiskStatus) => void>();
const setupListeners = new Set<(s: SetupProgress) => void>();

function publish(next: Partial<DiskStatus>) {
  status = { ...status, ...next, checked_ms: Date.now() };
  statusListeners.forEach((cb) => cb(status));
}
function publishSetup(next: Partial<SetupProgress>) {
  setup = { ...setup, ...next };
  setupListeners.forEach((cb) => cb(setup));
}
const later = (ms: number, fn: () => void) => void setTimeout(fn, ms);
const listen = <T>(set: Set<(v: T) => void>, cb: (v: T) => void) => {
  set.add(cb);
  return () => void set.delete(cb);
};
const job = (kind: "warmup" | "free", path: string) => ({
  kind,
  path,
  name: path.split("/").pop() ?? path,
  started_ms: Date.now(),
});

let login = true;

const SERVER = "https://floppy.studio-esempio.it";
const EMAIL = "giulia.ferri@studio-esempio.it";
const MAC = "MacBook Pro di Giulia";
const inMinutes = (m: number) => new Date(Date.now() + m * 60_000).toISOString();
const authBase = {
  server: SERVER,
  server_display: "floppy.studio-esempio.it",
  default_server: SERVER,
  busy: null,
  error: null,
  reachable: true,
} as const;
const codeSent: AuthView = {
  ...authBase,
  phase: "code_sent",
  email: EMAIL,
  device_name: MAC,
  expires_at: inMinutes(15),
  resend_at: inMinutes(1),
};
const signedIn: AuthView = {
  ...authBase,
  phase: "signed_in",
  email: EMAIL,
  device_name: MAC,
  organization: "Studio Esempio",
  user_name: "Giulia Ferri",
};
const signedOut: AuthView = { ...authBase, phase: "signed_out", notice: null };

const AUTH: Record<string, AuthView> = {
  auth_signed_out: signedOut,
  auth_code_sent: codeSent,
  auth_wrong_code: { ...codeSent, error: { code: "invalid_code", attempts_left: 3 } },
  auth_checking: { ...codeSent, busy: "checking" },
  auth_expired: { ...authBase, phase: "ended", reason: "expired", email: EMAIL, device_name: MAC },
  auth_locked: { ...authBase, phase: "ended", reason: "locked", email: EMAIL, device_name: MAC },
  auth_elsewhere: { ...authBase, phase: "ended", reason: "elsewhere", email: EMAIL, device_name: MAC },
  auth_rate_limited: { ...signedOut, error: { code: "rate_limited", until_ms: Date.now() + 42_000 } },
  auth_unreachable: {
    ...signedOut,
    error: { code: "unreachable", detail: "the server did not answer: connection refused" },
  },
  auth_registering: { ...authBase, phase: "registering", email: EMAIL, device_name: MAC, busy: "registering" },
  auth_register_failed: {
    ...authBase,
    phase: "registering",
    email: EMAIL,
    device_name: MAC,
    error: { code: "metadata_unavailable" },
  },
  auth_signed_in: signedIn,
  auth_signed_in_no_org: { ...signedIn, organization: null, user_name: null },
  auth_offline: { ...signedIn, reachable: false },
  auth_revoked: { ...signedOut, notice: "revoked" },
  auth_unknown: { ...signedOut, notice: "unknown" },
  auth_already_signed_out: { ...signedOut, notice: "already_signed_out" },
  auth_clearing: { ...signedIn, busy: "clearing" },
  auth_not_cleared: {
    ...signedOut,
    notice: "signed_out",
    error: { code: "disks_not_cleared", detail: "the floppy command failed: the VM is not responding" },
  },
  auth_sign_out_failed: {
    ...signedIn,
    error: { code: "sign_out_failed", detail: "the server did not answer: timed out" },
  },
  auth_bad_server: { ...signedOut, error: { code: "invalid_server", reason: "insecure" } },
};

// A team install before sign-in is signed out.
let auth: AuthView = AUTH[scenario] ?? (scenario === "team_no_disks" ? signedOut : signedIn);
const authListeners = new Set<(v: AuthView) => void>();
function publishAuth(next: AuthView): AuthView {
  auth = next;
  authListeners.forEach((cb) => cb(auth));
  return auth;
}
const settle = (next: AuthView) =>
  new Promise<AuthView>((resolve) => later(700, () => resolve(publishAuth(next))));

/** The browser stand-in for the Tauri backend. */
export const mockBackend: Backend = {
  view: () => params.get("view") ?? "panel",
  getStatus: async () => status,
  refresh: async () => publish({}),
  start: async () => {
    publish({ state: "starting", job: { kind: "start", path: null, name: null, started_ms: Date.now() } });
    later(1500, () => publish({ ...base, job: null }));
  },
  stop: async () => {
    publish({ state: "stopping", job: { kind: "stop", path: null, name: null, started_ms: Date.now() } });
    later(1500, () => publish({ state: "off", ...unmounted, job: null }));
  },
  warmup: async (path) => {
    publish({ job: job("warmup", path) });
    later(4000, () => publish({ job: null }));
  },
  free: async (path) => {
    publish({ job: job("free", path) });
    later(1500, () => publish({ job: null }));
  },
  cancel: async () => publish({ job: null }),
  dismissError: async () => publish({ last_error: null }),
  listFolders: async (path) =>
    scenario === "empty"
      ? []
      : path
        ? FOLDERS.slice(1, 4).map((f) => ({ name: f.name, path: `${path}/${f.name}` }))
        : FOLDERS,
  setDiskEnabled: async (id, enabled) => {
    if (!status.team) return;
    const disks = status.team.disks.map((d) =>
      d.id !== id ? d : enabled ? disk(d.folder, { access: d.access, mounted_access: d.access }) : { ...d, ...off },
    );
    const anyOn = disks.some((d) => d.enabled);
    publish({ team: { ...status.team, disks }, state: anyOn ? "connected" : "no_disks", mount_point: anyOn ? "/Volumes/Floppy" : null });
  },
  restart: async () => {
    publish({ state: "starting", job: { kind: "restart", path: null, name: null, started_ms: Date.now() } });
    later(1500, () => publish({ state: "connected", job: null, team: status.team && { ...status.team, restart_needed: false } }));
  },
  openInFinder: async () => {},
  getSetup: async () => setup,
  runSetup: async () => {
    publishSetup({ running: true, step: 1, done: false, error: null });
    later(1200, () => publishSetup({ step: 2 }));
    later(2400, () => publishSetup({ step: 3 }));
    later(3600, () => {
      publishSetup({ running: false, step: 4, done: true });
      publish({ ...base });
    });
  },
  installEngine: async () => {
    publish({ state: "starting", job: { kind: "install", path: null, name: null, started_ms: Date.now() } });
    publishSetup({ running: true, step: 0, done: false, error: null });
    later(1200, () => publishSetup({ step: 1 }));
    later(2400, () => publishSetup({ step: 2 }));
    later(3600, () => publishSetup({ step: 3 }));
    later(4800, () => {
      publishSetup({ running: false, step: 4, done: true });
      publish({ ...base });
    });
  },
  appInfo: async () => ({
    version: "0.1.0",
    engine_path: base.engine_path,
    computer_name: MAC,
  }),
  openSettings: async () => {},
  openWelcome: async () => {},
  finishWelcome: async () => {},
  loginEnabled: async () => login,
  setLogin: async (on) => {
    login = on;
  },
  notify: async () => {},
  setHeight: async () => {},
  onStatus: (cb) => listen(statusListeners, cb),
  onSetup: (cb) => listen(setupListeners, cb),
  onNotice: (cb: (n: Notice) => void) => {
    void cb;
    return () => {};
  },
  onPanelOpened: () => () => {},
  authView: async () => auth,
  authStart: async (email, deviceName) => {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      return publishAuth({ ...auth, error: { code: "invalid_email" } });
    }
    publishAuth({ ...auth, busy: "sending", error: null });
    return settle({ ...codeSent, email: email.trim().toLowerCase(), device_name: deviceName });
  },
  authResend: async () => publishAuth({ ...codeSent, resend_at: inMinutes(1) }),
  authBack: async () => publishAuth(signedOut),
  authSubmitCode: async (code) => {
    publishAuth({ ...auth, busy: "checking", error: null });
    if (code !== "123456") return settle({ ...auth, busy: null, error: { code: "invalid_code", attempts_left: 4 } });
    publishAuth({ ...authBase, phase: "registering", email: EMAIL, device_name: MAC, busy: "registering" });
    return settle(signedIn);
  },
  authRetryRegister: async () => settle(signedIn),
  authSignOut: async (localOnly) => {
    publishAuth({ ...auth, busy: "signing_out", error: null });
    if (scenario === "auth_sign_out_failed" && !localOnly) {
      return settle(AUTH.auth_sign_out_failed!);
    }
    return settle({ ...signedOut, notice: "signed_out" });
  },
  authSetServer: async (url) => {
    if (url && url.startsWith("http://")) {
      return publishAuth({ ...auth, error: { code: "invalid_server", reason: "insecure" } });
    }
    const server = url ? (url.includes("://") ? url : `https://${url}`) : SERVER;
    return publishAuth({ ...auth, server, server_display: server.replace(/^https?:\/\//, ""), error: null });
  },
  onAuth: (cb) => listen(authListeners, cb),
};
