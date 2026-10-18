/** Wire types, mirrored from src-tauri/src/core.rs and engine.rs. */

/** The disk as the app shows it. */
export type DiskState =
  | "not_installed"
  | "needs_setup"
  | "off"
  | "starting"
  | "stopping"
  | "connected"
  | "unreachable"
  /** Team version, ready, with no disk to mount (not signed in, none given, all turned off). */
  | "no_disks";

/** Why the disk is unreachable. */
export type Problem =
  | "vm_stopped"
  | "vm_broken"
  | "drive_down"
  | "not_mounted"
  | "not_responding"
  | "engine";

/** What the app has running: one job at a time; `install` runs the bundled install.sh, `restart` stop then start. */
export type JobKind = "install" | "setup" | "start" | "stop" | "restart" | "warmup" | "free";

/** Read-only or read-write, per person and disk. */
export type Access = "read" | "write";

/** A team disk's mount in the VM (engine.rs, `RunState`). */
export type RunState = "mounted" | "starting" | "failed" | "stopped" | "disabled" | "other";

/** One team disk: the person's choice from disks.json, the rest from the engine (core.rs, `DiskView`). */
export interface DiskView {
  id: string;
  folder: string;
  /** Its folder in the Finder, while mounted. */
  path: string | null;
  access: Access;
  enabled: boolean;
  /** null before the engine has reported the disk. */
  state: RunState | null;
  mounted_access: Access | null;
  pending: "remount" | "remove" | "other" | null;
  cache_used_mb: number | null;
  cache_size_mb: number | null;
  replica: "up" | "syncing" | "down" | "stopped" | "other" | null;
  meta_mode: "replica" | "master" | "offline" | "unknown" | "other" | null;
  online: boolean | null;
  /** The replica's lag, an upper bound; null when not mounted. */
  lag_ms: number | null;
  /** false: no longer given to this person, removed once nothing on it is open. */
  listed: boolean;
}

/** A disk the Mac may see but cannot mount. */
export interface RejectedDisk {
  id: string;
  name: string;
  reason: "name" | "duplicate_name" | "address" | "id";
}

/** The team's disks (core.rs, `TeamView`). */
export interface TeamView {
  disks: DiskView[];
  rejected: RejectedDisk[];
  /** `floppy disks apply` (or clear) is running. */
  applying: boolean;
  error: string | null;
  /** The VM needs more memory for its disks: Floppy has to restart. */
  restart_needed: boolean;
  /** The account has disks but this Mac runs the single-user volume. */
  refused_single: boolean;
  /** Disks in use that have stayed failed for a while: the faceplate shows a problem. */
  failing: number;
}

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
  /** The install this Mac runs, once the engine has answered. */
  mode: "single" | "team" | null;
  /** The team's disks, in the team version or when the account has some. */
  team: TeamView | null;
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
  | { phase: "signed_out"; notice: OutNotice | null }
  | { phase: "code_sent"; email: string; device_name: string; expires_at: string; resend_at: string }
  | { phase: "ended"; reason: EndReason; email: string; device_name: string }
  | { phase: "registering"; email: string; device_name: string }
  | { phase: "signed_in"; email: string; device_name: string; organization: string | null; user_name: string | null };

/** Why the app is signed out: revoked, by the person, unknown to the server, already signed out. */
export type OutNotice = "revoked" | "signed_out" | "unknown" | "already_signed_out";

/** The step running now. */
export type AuthBusy = "sending" | "checking" | "linking" | "registering" | "signing_out" | "clearing";

/** What went wrong in the last step (`AuthError`). */
export type AuthError =
  | { code: "invalid_email" | "invalid_device_name" | "not_resent" | "mail_failed" | "metadata_unavailable" }
  | { code: "invalid_server"; reason: "not_an_address" | "insecure" | "extra" }
  | { code: "unreachable" | "sign_out_failed" | "disks_not_cleared" | "keychain" | "settings" | "server"; detail: string }
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
    | "not_team"
    | "no_such_disk"
    | "engine"
    | "io"
    | "auth_busy"
    | "auth_state";
  message: string;
}
