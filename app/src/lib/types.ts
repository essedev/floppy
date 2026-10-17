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

/** What the app has running: one job at a time; `install` runs the bundled install.sh. */
export type JobKind = "install" | "setup" | "start" | "stop" | "warmup" | "free";

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
  /** What this Mac needs from the engine in the app (`installer.rs`), if anything. */
  install: "fresh" | "update" | null;
  /** The app carries an engine: a missing command can be installed. */
  bundled: boolean;
}

/** Where the install and `floppy setup` are: step 0 copy, 1 virtual disk, 2 start, 3 connect, 4 done. */
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

/** Facts about this app and this Mac. */
export interface AppInfo {
  version: string;
  engine_path: string;
  /** The Mac's name in Sharing settings: the default device name. */
  computer_name: string | null;
}

/** Why a waiting sign-in can no longer complete. */
export type EndReason = "expired" | "locked" | "elsewhere";

/** Where the sign-in is (src-tauri/src/account/mod.rs, `Phase`). */
export type AuthPhase =
  | { phase: "signed_out"; notice: "revoked" | "signed_out" | null }
  | { phase: "code_sent"; email: string; device_name: string; expires_at: string; resend_at: string }
  | { phase: "ended"; reason: EndReason; email: string; device_name: string }
  | { phase: "registering"; email: string; device_name: string }
  | { phase: "signed_in"; email: string; device_name: string; organization: string | null };

/** The step running now. */
export type AuthBusy = "sending" | "checking" | "linking" | "registering" | "signing_out";

/** What went wrong in the last step (`AuthError`). */
export type AuthError =
  | { code: "invalid_email" | "invalid_device_name" | "not_resent" | "mail_failed" | "metadata_unavailable" }
  | { code: "invalid_server"; reason: "not_an_address" | "insecure" | "extra" }
  | { code: "unreachable" | "sign_out_failed" | "keychain" | "settings" | "server"; detail: string }
  | { code: "rate_limited"; until_ms: number }
  | { code: "invalid_code"; attempts_left: number | null };

/** Everything the windows show about the account. */
export type AuthView = AuthPhase & {
  server: string;
  server_display: string;
  default_server: string;
  busy: AuthBusy | null;
  error: AuthError | null;
  /** The last heartbeat got an answer. */
  reachable: boolean;
};

/** A notification raised by the native side. */
export type Notice = { kind: "reconnected" } | { kind: "warmup_done"; name: string };

/** An error from a command, rendered by `code` from the dictionary. */
export interface CommandError {
  code:
    | "busy"
    | "not_mounted"
    | "not_on_disk"
    | "nothing_to_cancel"
    | "list_timeout"
    | "no_bundle"
    | "engine"
    | "io"
    | "auth_busy"
    | "auth_state";
  message: string;
}
