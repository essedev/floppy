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
import type { AppInfo, DiskStatus, Folder, Notice, SetupProgress } from "@/lib/types";

type Unlisten = () => void;

/** Everything the views ask of the native side. */
export interface Backend {
  /** Which window this is: panel, settings or welcome. */
  view(): string;
  getStatus(): Promise<DiskStatus>;
  refresh(): Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
  warmup(path: string): Promise<void>;
  free(path: string): Promise<void>;
  cancel(): Promise<void>;
  dismissError(): Promise<void>;
  listFolders(): Promise<Folder[]>;
  openInFinder(path?: string): Promise<void>;
  getSetup(): Promise<SetupProgress>;
  runSetup(): Promise<void>;
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
  listFolders: () => invoke("list_folders"),
  openInFinder: (path) => invoke("open_in_finder", { path: path ?? null }),
  getSetup: () => invoke("get_setup"),
  runSetup: () => invoke("run_setup"),
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
};

/** The backend for this environment. */
export const backend: Backend = isTauri() ? tauriBackend : mockBackend;
