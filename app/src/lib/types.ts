/** Wire types, mirrored from src-tauri/src/core.rs and engine.rs. */

/** The disk as the app shows it. */
export type DiskState =
  | "not_installed"
  | "needs_setup"
  | "off"
  | "starting"
  | "stopping"
  | "connected"
  | "unreachable";

/** Why the disk is unreachable. */
export type Problem =
  | "vm_stopped"
  | "vm_broken"
  | "drive_down"
  | "not_mounted"
  | "not_responding"
  | "engine";

/** What the app has running: one `floppy` command at a time. */
export type JobKind = "setup" | "start" | "stop" | "warmup" | "free";

/** Space on this Mac and in the cache, in MB, with the command's reserve. */
export interface Space {
  cache_used_mb: number | null;
  cache_limit_mb: number | null;
  mac_free_mb: number | null;
  reserve_mb: number;
  low: boolean;
}

/** The running job. */
export interface JobInfo {
  kind: JobKind;
  path: string | null;
  name: string | null;
  started_ms: number;
}

/** A job that ended with an error, until dismissed. */
export interface JobError {
  kind: JobKind;
  name: string | null;
  message: string;
}

/** Everything the panel, the windows and the menu bar icon show. */
export interface DiskStatus {
  state: DiskState;
  problem: Problem | null;
  mount_point: string | null;
  wrong_mount_point: boolean;
  space: Space | null;
  job: JobInfo | null;
  last_error: JobError | null;
  engine_error: string | null;
  next_check_s: number;
  checked_ms: number;
  engine_path: string;
}

/** Where `floppy setup` is: step 0 virtual disk, 1 start, 2 connect, 3 done. */
export interface SetupProgress {
  running: boolean;
  step: number;
  done: boolean;
  error: { kind: "space" | "other"; message: string } | null;
}

/** A folder at the top of the disk. */
export interface Folder {
  name: string;
  path: string;
}

/** The placeholder account until the control plane exists (`fake: true`). */
export interface Account {
  fake: boolean;
  name: string;
  initials: string;
  email: string;
  team: string;
  role: string;
}

/** Facts about this app and this Mac. */
export interface AppInfo {
  version: string;
  engine_path: string;
  computer_name: string | null;
  account: Account;
}

/** A notification raised by the native side. */
export type Notice = { kind: "reconnected" } | { kind: "warmup_done"; name: string };

/** An error from a command, rendered by `code` from the dictionary. */
export interface CommandError {
  code: "busy" | "not_mounted" | "not_on_disk" | "nothing_to_cancel" | "list_timeout" | "engine" | "io";
  message: string;
}
