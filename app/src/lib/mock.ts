/**
 * MOCK BACKEND, browser only. In the Vite dev server there is no Tauri, so the
 * views run on these invented states to be looked at and checked with
 * Playwright: `?view=panel|settings|welcome&scenario=<name>`. The account has
 * its own scenarios, `auth_<phase or error>` (signed in otherwise), and steps
 * through them on clicks: code 123456 signs in, any other is wrong. The team
 * version's are `team_<case>`, `team_activity` with the activity's notices
 * and a colleague's copy arriving. The disk window (`view=disk&tab=activity|trash`)
 * shows three team disks; its scenarios are `trash_empty`, `trash_error`,
 * `trash_none`, `activity_offline`, `activity_local` and `activity_empty`. The
 * app itself never uses this module. Folder names come from concept-a-panel
 * and concept-f.
 */

import type { Backend } from "@/lib/backend";
import type {
  AppUpdate,
  ActivityNotice,
  ActivitySnapshot,
  AuthView,
  DiskStatus,
  DiskView,
  FeedEvent,
  FeedPage,
  Folder,
  LiveFile,
  Notice,
  PersonRef,
  RestoreResult,
  SetupProgress,
  TeamView,
  TrashEntry,
  TrashReport,
} from "@/lib/types";

const params = new URLSearchParams(globalThis.location?.search ?? "");
const view = params.get("view") ?? "panel";
// ?update=available|failed: a newer release of the app, as the daily check finds it.
const updateParam = params.get("update");
let appUpdate: AppUpdate | null =
  updateParam === "available" || updateParam === "failed"
    ? { version: "0.2.2", state: updateParam, error: updateParam === "failed" ? "Error: Cask 'floppy' is not installed." : null }
    : null;
const updateListeners = new Set<(u: AppUpdate | null) => void>();
function publishUpdate(u: AppUpdate | null) {
  appUpdate = u;
  updateListeners.forEach((cb) => cb(u));
}
const scenario = params.get("scenario") ?? (view === "disk" ? "disk_team" : "connected");

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
    chosen_mb: null,
    choice_max_mb: null,
    target_mb: null,
    waits_restart: false,
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
    lag_ms: 500,
    listed: true,
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
  restart_reason: null,
  refused_single: false,
  failing: 0,
};
const teamSpace = { ...base.space!, cache_limit_mb: 45 * GB, chosen_mb: 45 * GB, choice_max_mb: 95 * GB, target_mb: 45 * GB };
const team = (over: Partial<TeamView> = {}): Partial<DiskStatus> => ({ mode: "team", team: { ...teamBase, ...over }, space: teamSpace });

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
      disk("Archivio 2024", { listed: false, enabled: false, pending: "remove", cache_used_mb: 2.2 * GB }),
    ],
  }),
  team_failed: team({
    failing: 1,
    disks: [
      disk("Archivio 2025", { access: "read", mounted_access: "read", cache_used_mb: 812 }),
      disk("Showreel 2026", { path: null, state: "failed", mounted_access: null, cache_used_mb: null, lag_ms: null }),
      disk("Pastificio Moretti - Spot Natale", { cache_used_mb: 31.6 * GB }),
    ],
  }),
  team_starting: { state: "starting", ...team({ applying: true, disks: teamBase.disks.map((d) => ({ ...d, path: null, state: null })) }), ...unmounted },
  team_restart: team({ restart_needed: true, restart_reason: "memory" }),
  team_space_restart: {
    ...team({ restart_needed: true, restart_reason: "space" }),
    space: { ...teamSpace, chosen_mb: 60 * GB, target_mb: 60 * GB, waits_restart: true },
  },
  team_space_short: { ...team(), space: { ...teamSpace, mac_free_mb: 31 * GB, chosen_mb: 45 * GB, choice_max_mb: 10 * GB, target_mb: 10 * GB } },
  team_error: team({ error: "the floppy command failed: the VM is not responding, run: floppy status" }),
  team_no_disks: { state: "no_disks", ...team({ disks: [] }), ...unmounted },
  team_all_off: {
    state: "no_disks",
    ...unmounted,
    ...team({ disks: teamBase.disks.map((d) => ({ ...d, ...off })) }),
  },
  team_single: { ...team({ refused_single: true }), mode: "single" },
  team_activity: team(),
};
// The disk window looks at the team's disks of concept-f.
const diskTeam = team({
  disks: [
    disk("Progetti", { id: "progetti", cache_used_mb: 12.4 * GB }),
    disk("Archivio 2025", { access: "read", mounted_access: "read", cache_used_mb: 812 }),
    disk("Consegne", { cache_used_mb: 1.1 * GB }),
  ],
});
for (const k of ["disk_team", "trash_empty", "trash_error", "trash_none", "activity_offline", "activity_local", "activity_empty"]) {
  SCENARIOS[k] = diskTeam;
}

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

const H = 3600;
const MB = 1024 * 1024;
/** A trash entry deleted `ago` seconds before now, from concept-f's lists. */
function gone(id: string, name: string, from: string | null, ago: number, size: number, over: Partial<TrashEntry> = {}): TrashEntry {
  const at = Date.now() / 1000 - ago;
  return { id, name, kind: "file", from, deleted_at: at, expires_at: at + 7 * 24 * H, size, replaced: false, by_this_mac: false, ...over };
}
const NO_TRASH = { disk: "consegne", folder: "Consegne", writable: true, trash_days: 7, entries: [] };
const TRASH: TrashReport = {
  disks: [
    {
      disk: "progetti",
      folder: "Progetti",
      writable: true,
      trash_days: 7,
      entries: [
        gone("t1", "Girato grezzo", "Pastificio Moretti - Spot Natale", 0.4 * H, 46.8 * 1024 * MB, { kind: "folder", files: 120, by_this_mac: true }),
        gone("t2", "Musica_licenze.xlsx", "Pastificio Moretti - Spot Natale/Documenti", 5 * H, 84 * 1024, { replaced: true, by: { name: "Marco Bassi", you: false } }),
        gone("t3", "Intervista_enologo.mov", "Cantine Arnaldi - Vendemmia/Girato", 6 * H, 3.9 * 1024 * MB, { by: { name: "Marco Bassi", you: false } }),
        gone("t4", "Drone_vigneto_02.mov", "Cantine Arnaldi - Vendemmia/Girato", 26 * H, 2.2 * 1024 * MB),
        gone("t5", "Sottotitoli_IT.srt", "Fondazione Lirica - Backstage Tosca/Export", 28 * H, 12 * 1024, { replaced: true, by_this_mac: true }),
        gone("t6", "Spot_Natale_v2.mp4", null, 30 * H, 1.4 * 1024 * MB, { by_this_mac: true }),
        gone("t7", "Logo_cliente_old.png", "", 6 * 24 * H, 640 * 1024),
      ],
    },
    {
      disk: "archivio-2025",
      folder: "Archivio 2025",
      writable: false,
      trash_days: 7,
      entries: [gone("a1", "Backup_progetto.zip", "Cantine Arnaldi", 3 * 24 * H, 7.1 * 1024 * MB)],
    },
    NO_TRASH,
  ],
};
const trashReport = (): TrashReport =>
  scenario === "trash_none"
    ? { disks: [] }
    : scenario === "trash_empty"
      ? { disks: [NO_TRASH] }
      : TRASH;

const MARCO: PersonRef = { name: "Marco Bassi", you: false };
const SIMONE: PersonRef = { name: "Simone", you: false };
const YOU: PersonRef = { name: "Giulia Ferri", you: true };
let feedId = 0;
/** An event `ago` hours before now on Progetti. */
function happened(ago: number, by: PersonRef, op: FeedEvent["op"], path: string, over: Partial<FeedEvent> = {}): FeedEvent {
  feedId += 1;
  return { id: `m${feedId}`, disk: "progetti", at: Date.now() / 1000 - ago * H, op, kind: "file", path, to: null, size: null, by, this_mac: by.you, ...over };
}
const FEED: FeedEvent[] = [
  happened(0.4, YOU, "deleted", "Pastificio Moretti - Spot Natale/Girato grezzo", { kind: "folder" }),
  ...Array.from({ length: 120 }, (_, i) =>
    happened(0.4 + i / 36000, YOU, "deleted", `Pastificio Moretti - Spot Natale/Girato grezzo/A00${1 + (i % 3)}_C${String(i + 1).padStart(3, "0")}_1017.mov`, {
      size: 390 * MB + i * MB,
    }),
  ),
  happened(1.6, MARCO, "renamed", "Fondazione Lirica - Backstage", { kind: "folder", to: "Fondazione Lirica - Backstage Tosca" }),
  ...Array.from({ length: 48 }, (_, i) =>
    happened(4 + i / 120, YOU, "added", `Cantine Arnaldi - Vendemmia/Girato/A002_C${String(i + 1).padStart(3, "0")}_1018.mov`, { size: 470 * MB }),
  ),
  happened(5, MARCO, "replaced", "Pastificio Moretti - Spot Natale/Documenti/Musica_licenze.xlsx", { size: 84 * 1024 }),
  happened(6, MARCO, "deleted", "Cantine Arnaldi - Vendemmia/Girato/Intervista_enologo.mov", { size: 3.9 * 1024 * MB }),
  happened(7, SIMONE, "renamed", "Logo_cliente.png", { to: "Showreel 2026/Grafiche/Logo_cliente.png" }),
  happened(26, SIMONE, "deleted", "Cantine Arnaldi - Vendemmia/Girato/Drone_vigneto_02.mov", { size: 2.2 * 1024 * MB }),
  happened(27, SIMONE, "created", "Showreel 2026/Grafiche", { kind: "folder" }),
];
const started = Date.now() / 1000;
/** What colleagues are writing now: a clip growing in 64 MB steps, an export. */
function liveNow(): LiveFile[] {
  const now = Date.now() / 1000;
  const size = (512 + Math.floor((now - started) / 8) * 64) * MB;
  return [
    {
      disk: "progetti",
      path: "Cantine Arnaldi - Vendemmia/Girato/A001_C014_1017.mov",
      op: "added",
      since: now - 64,
      size,
      speed: size / (now - started + 64),
      by: MARCO,
      this_mac: false,
    },
    {
      disk: "progetti",
      path: "Fondazione Lirica - Backstage Tosca/Export/Backstage_finale.mp4",
      op: "written",
      since: now - 300,
      size: 1.2 * 1024 * MB,
      speed: null,
      by: SIMONE,
      this_mac: false,
    },
    // The panel's team disks in team_activity: Showreel 2026 shows it on Cantine's row.
    {
      disk: "showreel-2026",
      path: "Cantine Arnaldi - Vendemmia/Girato/A001_C014_1017.mov",
      op: "added",
      since: now - 64,
      size,
      speed: size / (now - started + 64),
      by: MARCO,
      this_mac: false,
    },
  ];
}
const noticeBase = { can_undo: true, state: "open", error: null, finder_path: null } as const;
let notices: ActivityNotice[] =
  scenario === "team_activity"
    ? [
        {
          ...noticeBase,
          id: 1,
          kind: "mass_delete",
          folder: "Pastificio Moretti - Spot Natale",
          disk: "pastificio-moretti-spot-natale",
          items: [{ path: "Girato grezzo", at: started - 60 }],
          name: "Girato grezzo",
          files: 120,
          bytes: 46.8 * 1024 * MB,
          at: started - 60,
        },
        {
          ...noticeBase,
          id: 2,
          kind: "renamed",
          folder: "Showreel 2026",
          disk: "showreel-2026",
          path: "Fondazione Lirica - Backstage",
          to: "Fondazione Lirica - Backstage Tosca",
          by: MARCO,
          at: started - 120,
        },
        {
          ...noticeBase,
          id: 3,
          kind: "renamed_back",
          can_undo: false,
          folder: "Showreel 2026",
          disk: "showreel-2026",
          path: "Clienti",
          to: "Clienti 2026",
          by: SIMONE,
        },
      ]
    : [];
const activityListeners = new Set<(s: ActivitySnapshot) => void>();
const snapshot = (): ActivitySnapshot => ({ notices, live: scenario === "team_activity" || view === "disk" ? liveNow() : [] });
function publishActivity(next: ActivityNotice[]) {
  notices = next;
  activityListeners.forEach((cb) => cb(snapshot()));
}
if (scenario === "team_activity" || view === "disk") setInterval(() => publishActivity(notices), 8000);
const setNotice = (id: number, over: Partial<ActivityNotice>) =>
  publishActivity(notices.map((x) => (x.id === id ? ({ ...x, ...over } as ActivityNotice) : x)));
const feedPage = (disk: string | null, before: string | null): FeedPage => {
  const events = scenario === "activity_empty" || disk !== "progetti" ? [] : FEED;
  const mine = events.filter((e) => e.by.you);
  const offline = scenario === "activity_offline";
  const local = scenario === "activity_local";
  const list = offline || local ? mine : events;
  // Two pages, to show "Mostra le precedenti".
  const cut = list.findIndex((e) => e.at < Date.now() / 1000 - 24 * H);
  const page = before ? list.slice(cut) : cut > 0 ? list.slice(0, cut) : list;
  return {
    events: page,
    before: !before && cut > 0 ? "older" : null,
    shared: !offline && !local,
    offline_since_ms: offline ? Date.now() - 20 * 60_000 : null,
    live: liveNow().filter((l) => l.disk === disk),
  };
};

/** The browser stand-in for the Tauri backend. */
export const mockBackend: Backend = {
  view: () => view,
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
  setCacheSize: async (mb) => {
    if (!status.space || !status.team) return;
    const limit = status.space.cache_limit_mb ?? 0;
    const bigger = mb > limit;
    publish({
      space: { ...status.space, chosen_mb: mb, target_mb: mb, cache_limit_mb: bigger ? limit : mb, waits_restart: bigger },
      team: { ...status.team, restart_needed: bigger, restart_reason: bigger ? "space" : null },
    });
  },
  restart: async () => {
    publish({ state: "starting", job: { kind: "restart", path: null, name: null, started_ms: Date.now() } });
    later(1500, () =>
      publish({
        state: "connected",
        job: null,
        team: status.team && { ...status.team, restart_needed: false, restart_reason: null },
        space: status.space && { ...status.space, cache_limit_mb: status.space.target_mb ?? status.space.cache_limit_mb, waits_restart: false },
      }),
    );
  },
  openInFinder: async () => {},
  revealInFinder: async () => {},
  getTrash: () =>
    new Promise<TrashReport>((resolve, reject) =>
      later(400, () => (scenario === "trash_error" ? reject({ code: "engine", message: "the VM is not responding" }) : resolve(trashReport()))),
    ),
  restoreTrash: (disk, ids) =>
    new Promise<RestoreResult>((resolve, reject) =>
      later(600, () => {
        const d = TRASH.disks.find((x) => x.disk === disk);
        if (!d) return reject({ code: "engine", message: "floppy-activity: disk " + disk + " is not mounted" });
        const out: RestoreResult = { restored: [], failed: [] };
        for (const id of ids) {
          const e = d.entries.find((x) => x.id === id);
          if (!e) {
            out.failed.push({ id, error: "not in the trash" });
            continue;
          }
          const name = e.replaced ? e.name.replace(/(\.[^.]+)?$/, " (ripristinato)$1") : e.name;
          const dir = e.from ?? "";
          const path = dir ? `${dir}/${name}` : name;
          out.restored.push({ id, disk, path, name, renamed: e.replaced, moved: e.from === null, finder_path: `/Volumes/Floppy/${d.folder}/${path}` });
        }
        resolve(out);
      }),
    ),
  openDisk: async () => {},
  onDiskWindow: (cb) => {
    const tab = params.get("tab");
    if (tab === "trash" || tab === "activity") later(0, () => cb({ disk: params.get("disk"), tab }));
    return () => {};
  },
  getActivity: (disk, before) =>
    new Promise((resolve) => later(300, () => resolve(feedPage(disk, before)))),
  getActivitySnapshot: async () => snapshot(),
  onActivity: (cb) => listen(activityListeners, cb),
  onAlert: () => () => {},
  restoreDeleted: (disk, items) =>
    new Promise<RestoreResult>((resolve) =>
      later(700, () =>
        resolve({
          restored: items.map((i, n) => ({
            id: `x${n}`,
            disk,
            path: i.path,
            name: i.path.split("/").pop() ?? i.path,
            renamed: false,
            moved: false,
            finder_path: `/Volumes/Floppy/Progetti/${i.path}`,
          })),
          failed: [],
        }),
      ),
    ),
  renameBack: (_disk, path) => new Promise((resolve) => later(600, () => resolve(path.startsWith("Fondazione") ? null : "taken"))),
  undoNotice: async (id) => {
    setNotice(id, { state: "working" });
    later(900, () => {
      const x = notices.find((y) => y.id === id);
      const path = x && "path" in x ? x.path : x && x.kind === "mass_delete" ? x.items[0]!.path : "";
      setNotice(id, { state: "done", finder_path: `/Volumes/Floppy/${x?.folder ?? ""}/${path}` });
    });
  },
  dismissNotice: async (id) => publishActivity(notices.filter((x) => x.id !== id)),
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
  getAppUpdate: async () => appUpdate,
  installAppUpdate: async () => {
    if (!appUpdate) return;
    publishUpdate({ ...appUpdate, state: "installing", error: null });
    later(2500, () =>
      publishUpdate({ ...appUpdate!, state: "failed", error: "Error: Cask 'floppy' is not installed." }),
    );
  },
  onAppUpdate: (cb) => listen(updateListeners, cb),
  openSettings: async () => {},
  openDashboard: async () => {},
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
