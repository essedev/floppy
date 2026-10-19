/**
 * The app's only door to the native side: Tauri commands and events in the
 * app, the mock in `mock.ts` in a plain browser (the Vite dev server, for
 * looking at the views and for the Playwright checks).
 */

import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";
import * as autostart from "@tauri-apps/plugin-autostart";
import * as notification from "@tauri-apps/plugin-notification";
import { mockBackend } from "@/lib/mock";
import type {
  ActivitySnapshot,
  Alert,
  AppInfo,
  AuthView,
  DeletedItem,
  DiskStatus,
  DiskWindowTarget,
  FeedPage,
  Folder,
  Notice,
  RenameRefusal,
  RestoreResult,
  SetupProgress,
  TrashReport,
} from "@/lib/types";

type Unlisten = () => void;

/** Everything the views ask of the native side. */
export interface Backend {
  /** Which window this is: panel, settings, disk or welcome. */
  view(): string;
  getStatus(): Promise<DiskStatus>;
  refresh(): Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
  warmup(path: string): Promise<void>;
  free(path: string): Promise<void>;
  cancel(): Promise<void>;
  dismissError(): Promise<void>;
  /** The folders at the top of the disk, or of `path` on it (a team disk's projects). */
  listFolders(path?: string): Promise<Folder[]>;
  /** Turns a team disk on or off on this Mac. */
  setDiskEnabled(id: string, enabled: boolean): Promise<void>;
  /** Stops and starts Floppy, for the memory its disks want. */
  restart(): Promise<void>;
  openInFinder(path?: string): Promise<void>;
  /** Shows a file on the disk in the Finder, selected. */
  revealInFinder(path: string): Promise<void>;
  /** Every mounted disk's trash (DECISIONS #27). */
  getTrash(): Promise<TrashReport>;
  /** Puts a trash entry back, never over a file; `disk` is null for the single-user volume. */
  restoreTrash(disk: string | null, ids: string[]): Promise<RestoreResult>;
  /** Opens the disk window on a tab, on one disk when given. */
  openDisk(disk: string | null, tab: DiskWindowTarget["tab"]): Promise<void>;
  /** The disk and tab the disk window was asked to show. */
  onDiskWindow(cb: (t: DiskWindowTarget) => void): Unlisten;
  /** A page of a disk's activity, newest first; `disk` null for the single-user volume. */
  getActivity(disk: string | null, before: string | null): Promise<FeedPage>;
  /** The panel's notices and what is being written now, also sent as "activity". */
  getActivitySnapshot(): Promise<ActivitySnapshot>;
  onActivity(cb: (s: ActivitySnapshot) => void): Unlisten;
  onAlert(cb: (a: Alert) => void): Unlisten;
  /** Puts back from the trash what events deleted. */
  restoreDeleted(disk: string | null, items: DeletedItem[]): Promise<RestoreResult>;
  /** Gives `to` its name of before, `path`; the refusal, or null when done. */
  renameBack(disk: string | null, path: string, to: string): Promise<RenameRefusal | null>;
  undoNotice(id: number): Promise<void>;
  dismissNotice(id: number): Promise<void>;
  getSetup(): Promise<SetupProgress>;
  runSetup(): Promise<void>;
  /** Installs or updates the engine from the app, then sets it up. */
  installEngine(): Promise<void>;
  appInfo(): Promise<AppInfo>;
  openSettings(): Promise<void>;
  openWelcome(): Promise<void>;
  finishWelcome(): Promise<void>;
  loginEnabled(): Promise<boolean>;
  setLogin(on: boolean): Promise<void>;
  notify(title: string, body: string): Promise<void>;
  setHeight(px: number): Promise<void>;
  onStatus(cb: (s: DiskStatus) => void): Unlisten;
  onSetup(cb: (s: SetupProgress) => void): Unlisten;
  onNotice(cb: (n: Notice) => void): Unlisten;
  onPanelOpened(cb: () => void): Unlisten;
  /** The account; every step answers with the new view, also sent as "auth". */
  authView(): Promise<AuthView>;
  authStart(email: string, deviceName: string): Promise<AuthView>;
  authResend(): Promise<AuthView>;
  authBack(): Promise<AuthView>;
  authSubmitCode(code: string): Promise<AuthView>;
  authRetryRegister(): Promise<AuthView>;
  authSignOut(localOnly: boolean): Promise<AuthView>;
  /** null goes back to the build's default server. */
  authSetServer(url: string | null): Promise<AuthView>;
  onAuth(cb: (v: AuthView) => void): Unlisten;
}

function subscribe<T>(event: string, cb: (payload: T) => void): Unlisten {
  let off: Unlisten | null = null;
  let gone = false;
  void listen<T>(event, (e) => cb(e.payload)).then((u) => {
    if (gone) u();
    else off = u;
  });
  return () => {
    gone = true;
    off?.();
  };
}

const tauriBackend: Backend = {
  view: () => getCurrentWindow().label,
  getStatus: () => invoke("get_status"),
  refresh: () => invoke("refresh_status"),
  start: () => invoke("start_disk"),
  stop: () => invoke("stop_disk"),
  warmup: (path) => invoke("warmup", { path }),
  free: (path) => invoke("free", { path }),
  cancel: () => invoke("cancel_job"),
  dismissError: () => invoke("dismiss_error"),
  listFolders: (path) => invoke("list_folders", { path: path ?? null }),
  setDiskEnabled: (id, enabled) => invoke("set_disk_enabled", { id, enabled }),
  restart: () => invoke("restart_disk"),
  openInFinder: (path) => invoke("open_in_finder", { path: path ?? null }),
  revealInFinder: (path) => invoke("reveal_in_finder", { path }),
  getTrash: () => invoke("get_trash"),
  restoreTrash: (disk, ids) => invoke("restore_trash", { disk, ids }),
  openDisk: (disk, tab) => invoke("open_disk", { disk, tab }),
  onDiskWindow: (cb) => subscribe("disk-window", cb),
  getActivity: (disk, before) => invoke("get_activity", { disk, before }),
  getActivitySnapshot: () => invoke("get_activity_snapshot"),
  onActivity: (cb) => subscribe("activity", cb),
  onAlert: (cb) => subscribe("alert", cb),
  restoreDeleted: (disk, items) => invoke("restore_deleted", { disk, items }),
  renameBack: (disk, path, to) => invoke("rename_back", { disk, path, to }),
  undoNotice: (id) => invoke("undo_notice", { id }),
  dismissNotice: (id) => invoke("dismiss_notice", { id }),
  getSetup: () => invoke("get_setup"),
  runSetup: () => invoke("run_setup"),
  installEngine: () => invoke("install_engine"),
  appInfo: () => invoke("app_info"),
  openSettings: () => invoke("open_settings"),
  openWelcome: () => invoke("open_welcome"),
  finishWelcome: () => invoke("finish_welcome"),
  loginEnabled: () => autostart.isEnabled(),
  setLogin: (on) => (on ? autostart.enable() : autostart.disable()),
  notify: async (title, body) => {
    let granted = await notification.isPermissionGranted();
    if (!granted) granted = (await notification.requestPermission()) === "granted";
    if (granted) notification.sendNotification({ title, body });
  },
  setHeight: (px) => getCurrentWindow().setSize(new LogicalSize(352, Math.ceil(px))),
  onStatus: (cb) => subscribe("status", cb),
  onSetup: (cb) => subscribe("setup", cb),
  onNotice: (cb) => subscribe("notify", cb),
  onPanelOpened: (cb) => subscribe("panel-opened", () => cb()),
  authView: () => invoke("auth_view"),
  authStart: (email, deviceName) => invoke("auth_start", { email, deviceName }),
  authResend: () => invoke("auth_resend"),
  authBack: () => invoke("auth_back"),
  authSubmitCode: (code) => invoke("auth_submit_code", { code }),
  authRetryRegister: () => invoke("auth_retry_register"),
  authSignOut: (localOnly) => invoke("auth_sign_out", { localOnly }),
  authSetServer: (url) => invoke("auth_set_server", { url }),
  onAuth: (cb) => subscribe("auth", cb),
};

/** The backend for this environment. */
export const backend: Backend = isTauri() ? tauriBackend : mockBackend;
