/**
 * MOCK BACKEND, browser only. In the Vite dev server there is no Tauri, so the
 * views run on these invented states to be looked at and checked with
 * Playwright: `?view=panel|settings|welcome&scenario=<name>`. The app itself
 * never uses this module. Folder names come from concept-a-panel.
 */

import type { Backend } from "@/lib/backend";
import type { DiskStatus, Folder, Notice, SetupProgress } from "@/lib/types";

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
};

const FOLDERS: Folder[] = [
  "Archivio 2025",
  "Cantine Arnaldi - Vendemmia",
  "Fondazione Lirica - Backstage",
  "Pastificio Moretti - Spot Natale",
  "Showreel 2026",
].map((name) => ({ name, path: `/Volumes/Floppy/${name}` }));

const unmounted = { mount_point: null, space: { ...base.space!, cache_used_mb: null, cache_limit_mb: null } };

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
  not_installed: { state: "not_installed", ...unmounted, space: null },
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
};

let status: DiskStatus = { ...base, ...(SCENARIOS[scenario] ?? {}) };
let setup: SetupProgress = {
  running: scenario === "setup_running",
  step: scenario === "setup_running" ? 1 : 0,
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
  listFolders: async () => (scenario === "empty" ? [] : FOLDERS),
  openInFinder: async () => {},
  getSetup: async () => setup,
  runSetup: async () => {
    publishSetup({ running: true, step: 0, done: false, error: null });
    later(1200, () => publishSetup({ step: 1 }));
    later(2400, () => publishSetup({ step: 2 }));
    later(3600, () => {
      publishSetup({ running: false, step: 3, done: true });
      publish({ ...base });
    });
  },
  appInfo: async () => ({
    version: "0.1.0",
    engine_path: base.engine_path,
    computer_name: "MacBook Pro di Giulia",
    account: {
      fake: true,
      name: "Giulia Ferri",
      initials: "GF",
      email: "giulia.ferri@studio.example",
      team: "Studio Esempio",
      role: "member",
    },
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
};
