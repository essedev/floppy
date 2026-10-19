//! The engine is the `floppy` command (`macos/bin/floppy`, installed in
//! `~/.floppy/bin/floppy` from the app's bundle, see `installer`). The app never touches the VM, the mount, launchd
//! or `nsmb.conf` itself: it runs the command and reads what it answers.
//! The team disks' contract (`status --json` with `disks`, `disks apply` with
//! the secrets on stdin, `disks clear`) is docs/ARCHITECTURE.md, "Team disks".
//!
//! No Tauri dependency here, so the module is tested against fake commands.

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Read};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

/// Overrides where the command lives: a fake script in tests and development.
pub const ENGINE_ENV: &str = "FLOPPY_BIN";
/// Overrides the engine's home, `~/.floppy`; the command honors it too.
pub const HOME_ENV: &str = "FLOPPY_HOME";
/// Where the disk must be mounted: CapCut projects reference media by full path.
pub const EXPECTED_MOUNT_POINT: &str = "/Volumes/Floppy";
/// The command's default when `config.env` sets no `RESERVE_MB`.
pub const DEFAULT_RESERVE_MB: u64 = 20480;
/// `status --json` waits at most 5 s on a dead mount and makes one call into
/// the VM; anything past this is a hung command.
pub const STATUS_TIMEOUT: Duration = Duration::from_secs(25);
/// `disks apply` and `disks clear` wait up to 15 minutes for the engine's lock
/// (another command running), and an apply may set the VM up first.
pub const DISKS_TIMEOUT: Duration = Duration::from_secs(30 * 60);
/// The PATH the command runs with, the same whether the app was started by
/// launchd (which gives this one) or from a terminal (which gives the user's).
const ENGINE_PATH_VAR: &str = "/usr/bin:/bin:/usr/sbin:/sbin";

/// The `floppy` command to run: `FLOPPY_BIN`, else `bin/floppy` in the
/// engine's home.
pub fn engine_path() -> PathBuf {
    if let Some(p) = std::env::var_os(ENGINE_ENV).filter(|p| !p.is_empty()) {
        return PathBuf::from(p);
    }
    floppy_home().join("bin/floppy")
}

/// The engine's home: `FLOPPY_HOME`, else `~/.floppy`.
pub fn floppy_home() -> PathBuf {
    std::env::var_os(HOME_ENV)
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".floppy"))
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// True when `path` is a file with an executable bit.
pub fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// Why the command could not give an answer.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum EngineError {
    #[error("the floppy command is not installed at {0}")]
    Missing(String),
    #[error("could not run the floppy command: {0}")]
    Spawn(String),
    #[error("the floppy command did not answer within {0} s")]
    Timeout(u64),
    #[error("the floppy command failed: {0}")]
    Failed(String),
    #[error("unexpected answer from the floppy command: {0}")]
    Parse(String),
}

/// What `floppy status --json` prints (version 1). An engine from before the
/// team version prints no `mode`, `vm_memory_*` nor `disks`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct EngineReport {
    pub version: u32,
    #[serde(default)]
    pub mode: Mode,
    /// Single-user: always. Team: the disks file exists.
    pub configured: bool,
    pub installed: bool,
    pub stopped: bool,
    pub vm: VmState,
    pub mount_point: Option<String>,
    pub responding: Option<bool>,
    /// Team: `active` only when Samba and every enabled disk are up.
    pub drive: Option<String>,
    pub cache_used_mb: Option<u64>,
    pub cache_limit_mb: Option<u64>,
    pub mac_free_mb: Option<u64>,
    pub reserve_mb: Option<u64>,
    #[serde(default)]
    pub vm_memory_mib: Option<u64>,
    #[serde(default)]
    pub vm_memory_wanted_mib: Option<u64>,
    /// Team: one entry per disk of the disks file; null when the VM is not running.
    #[serde(default)]
    pub disks: Option<Vec<EngineDisk>>,
}

/// Which install this Mac runs: the zip's single-user volume (`config.env`)
/// or the team version.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Single,
    Team,
}

/// How a team disk is mounted: `read` is `--read-only`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Access {
    Read,
    Write,
}

/// A team disk's mount in the VM.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunState {
    Mounted,
    /// The unit is coming up or retrying.
    Starting,
    Failed,
    Stopped,
    /// Turned off by the person (`enabled: false`).
    Disabled,
    #[serde(other)]
    Other,
}

/// A change the disk waits for until nothing on it is open.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Pending {
    /// New access, address, secrets or a smaller cache.
    Remount,
    /// Turned off or no longer visible, still in use.
    Remove,
    #[serde(other)]
    Other,
}

/// The disk's local Valkey replica.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Replica {
    Up,
    Syncing,
    /// No link to the master.
    Down,
    Stopped,
    #[serde(other)]
    Other,
}

/// Where the mount reads its metadata from (the patch's mode).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MetaMode {
    Replica,
    /// The replica is behind or down: reads go to the master.
    Master,
    /// The master is unreachable: reads from the replica, writes refused.
    Offline,
    /// Right after mounting.
    Unknown,
    #[serde(other)]
    Other,
}

/// One team disk as `status --json` and `disks apply` report it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineDisk {
    pub id: String,
    pub folder: String,
    /// The access asked for in the disks file.
    pub access: Access,
    pub enabled: bool,
    pub state: RunState,
    /// The access of the running mount; differs from `access` while a remount waits.
    pub mounted_access: Option<Access>,
    pub pending: Option<Pending>,
    pub cache_used_mb: Option<u64>,
    pub cache_size_mb: Option<u64>,
    pub replica: Replica,
    pub mode: Option<MetaMode>,
    /// False only when offline, null when not mounted.
    pub online: Option<bool>,
    /// The replica's lag, an upper bound; null when not mounted.
    #[serde(default)]
    pub lag_ms: Option<u64>,
    /// False for a disk the disks file no longer lists that the VM still
    /// has (its removal waiting while it is in use). An older engine omits it.
    #[serde(default = "listed_default")]
    pub listed: bool,
}

fn listed_default() -> bool {
    true
}

/// What `floppy disks apply` prints.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ApplyReport {
    pub version: u32,
    pub mount_point: Option<String>,
    pub cache_per_disk_mb: Option<u64>,
    pub vm_memory_mib: Option<u64>,
    pub vm_memory_wanted_mib: Option<u64>,
    /// The VM runs with less memory than its disks want: a restart applies it.
    pub restart_needed: bool,
    pub vm: ApplyVm,
}

/// The VM's side of an apply.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ApplyVm {
    pub smbd: Option<String>,
    pub drive: Option<String>,
    pub disks: Vec<EngineDisk>,
}

impl EngineReport {
    /// The team disks the report lists, none in the single-user mode.
    pub fn team_disks(&self) -> &[EngineDisk] {
        match (self.mode, &self.disks) {
            (Mode::Team, Some(d)) => d,
            _ => &[],
        }
    }

    /// The VM runs with less memory than its disks want: `floppy stop` and
    /// `start` give it the new size (it never changes while running).
    pub fn restart_needed(&self) -> bool {
        self.mode == Mode::Team
            && matches!(
                (self.vm_memory_mib, self.vm_memory_wanted_mib),
                (Some(have), Some(want)) if have < want
            )
    }
}

/// The VM as Lima reports it, `broken` when it runs but does not answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VmState {
    Missing,
    Running,
    Stopped,
    Broken,
    #[serde(other)]
    Other,
}

/// The last line of the output that holds a JSON object.
fn json_line(stdout: &str) -> Result<&str, EngineError> {
    stdout
        .lines()
        .rev()
        .find(|l| l.trim_start().starts_with('{'))
        .ok_or_else(|| EngineError::Parse("no JSON object in the output".into()))
}

/// The report in the command's output: the last line that holds a JSON object.
pub fn parse_report(stdout: &str) -> Result<EngineReport, EngineError> {
    let report: EngineReport =
        serde_json::from_str(json_line(stdout)?).map_err(|e| EngineError::Parse(e.to_string()))?;
    if report.version != 1 {
        return Err(EngineError::Parse(format!(
            "status version {} is not supported",
            report.version
        )));
    }
    Ok(report)
}

/// The answer of `floppy disks apply`.
pub fn parse_apply(stdout: &str) -> Result<ApplyReport, EngineError> {
    let report: ApplyReport =
        serde_json::from_str(json_line(stdout)?).map_err(|e| EngineError::Parse(e.to_string()))?;
    if report.version != 1 {
        return Err(EngineError::Parse(format!(
            "apply version {} is not supported",
            report.version
        )));
    }
    Ok(report)
}

/// A command run to its end, with its output.
#[derive(Debug)]
pub struct Finished {
    pub status: ExitStatus,
    pub stdout: String,
    pub stderr: String,
}

/// A command with the fixed PATH, piped output and its own process group.
pub fn command(bin: &Path, args: &[&str]) -> Command {
    let mut cmd = Command::new(bin);
    cmd.args(args)
        .env("PATH", ENGINE_PATH_VAR)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // Its own process group, so a hung command is stopped together with
        // the limactl and ssh processes it started.
        .process_group(0);
    cmd
}

/// Starts the command in its own process group, with stdout and stderr piped.
pub fn spawn(bin: &Path, args: &[&str]) -> Result<Child, EngineError> {
    if !is_executable(bin) {
        return Err(EngineError::Missing(bin.display().to_string()));
    }
    spawn_command(command(bin, args))
}

/// Starts a command built with `command`.
pub fn spawn_command(mut cmd: Command) -> Result<Child, EngineError> {
    cmd.spawn().map_err(|e| EngineError::Spawn(e.to_string()))
}

/// Sends `signal` to the whole process group of a command started by `spawn`.
pub fn signal_group(pid: u32, signal: i32) {
    if let Ok(pid) = i32::try_from(pid) {
        // SAFETY: kill(2) with a negative pid signals a process group; it has
        // no memory effects on this process.
        unsafe {
            libc::kill(-pid, signal);
        }
    }
}

fn drain<R: Read + Send + 'static>(pipe: Option<R>) -> thread::JoinHandle<String> {
    thread::spawn(move || {
        let mut out = String::new();
        if let Some(mut p) = pipe {
            let _ = p.read_to_string(&mut out);
        }
        out
    })
}

/// Runs the command and waits at most `timeout`; past it the process group is
/// killed and the call fails.
pub fn run(bin: &Path, args: &[&str], timeout: Duration) -> Result<Finished, EngineError> {
    run_with_input(bin, args, None, timeout)
}

/// `run`, with `input` written to the command's stdin and then closed: the
/// only way a secret reaches the engine (never argv, the environment or a file).
pub fn run_with_input(
    bin: &Path,
    args: &[&str],
    input: Option<&[u8]>,
    timeout: Duration,
) -> Result<Finished, EngineError> {
    if !is_executable(bin) {
        return Err(EngineError::Missing(bin.display().to_string()));
    }
    let mut cmd = command(bin, args);
    if input.is_some() {
        cmd.stdin(Stdio::piped());
    }
    let mut child = spawn_command(cmd)?;
    if let (Some(bytes), Some(mut stdin)) = (input, child.stdin.take()) {
        use std::io::Write;
        // A command that exits without reading gets EPIPE here: its exit
        // status says what went wrong, not the write. Dropping closes stdin.
        let _ = stdin.write_all(bytes);
    }
    let stdout = drain(child.stdout.take());
    let stderr = drain(child.stderr.take());
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() >= timeout => {
                signal_group(child.id(), libc::SIGKILL);
                let _ = child.wait();
                return Err(EngineError::Timeout(timeout.as_secs()));
            }
            Ok(None) => thread::sleep(Duration::from_millis(40)),
            Err(e) => return Err(EngineError::Spawn(e.to_string())),
        }
    };
    Ok(Finished {
        status,
        stdout: stdout.join().unwrap_or_default(),
        stderr: stderr.join().unwrap_or_default(),
    })
}

/// The engine's report, from `floppy status --json`.
pub fn status(bin: &Path) -> Result<EngineReport, EngineError> {
    let done = run(bin, &["status", "--json"], STATUS_TIMEOUT)?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    parse_report(&done.stdout)
}

fn failed(done: &Finished) -> EngineError {
    EngineError::Failed(
        last_message(&done.stderr)
            .unwrap_or_else(|| format!("exit {}", done.status.code().unwrap_or(-1))),
    )
}

/// `floppy disks apply`: the disks file (already written) and, when known,
/// the secrets on stdin. None keeps the secrets the VM already has.
pub fn apply(bin: &Path, secrets: Option<&[u8]>) -> Result<ApplyReport, EngineError> {
    // An empty stdin rather than an inherited one: the command reads it to the end.
    let done = run_with_input(
        bin,
        &["disks", "apply"],
        Some(secrets.unwrap_or_default()),
        DISKS_TIMEOUT,
    )?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    parse_apply(&done.stdout)
}

/// `floppy disks clear`: every team disk off this Mac, its cache and replica
/// deleted in the VM, the disks file gone. The buckets are not touched.
pub fn clear(bin: &Path) -> Result<(), EngineError> {
    let done = run(bin, &["disks", "clear"], DISKS_TIMEOUT)?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    Ok(())
}

/// Listing the trash resolves each deleted entry's folder with one `juicefs info` in the VM.
pub const TRASH_TIMEOUT: Duration = Duration::from_secs(60);
/// A restore renames entries in the VM's metadata; a large folder is many renames.
pub const RESTORE_TIMEOUT: Duration = Duration::from_secs(5 * 60);

/// What `floppy trash` prints: every mounted disk's trash (DECISIONS #27).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct TrashReport {
    pub disks: Vec<TrashDisk>,
}

/// One disk's trash; `disk` is null for the single-user volume.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct TrashDisk {
    pub disk: Option<String>,
    pub folder: Option<String>,
    /// False when this Mac mounts the disk read-only: nothing can be restored.
    pub writable: bool,
    pub trash_days: u32,
    pub entries: Vec<TrashEntry>,
}

/// A deleted file or folder, or the version a save replaced.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct TrashEntry {
    /// "<hour>/<parent inode>-<inode>": what `floppy trash restore` takes.
    pub id: String,
    pub name: String,
    pub kind: TrashKind,
    /// The folder it was in, relative to the disk ("" at the top); null when that
    /// folder is gone, and the entry comes back at the top.
    pub from: Option<String>,
    /// Unix seconds.
    pub deleted_at: f64,
    pub expires_at: f64,
    /// Bytes; a folder's is everything deleted inside it.
    pub size: u64,
    /// A file of the same name is where it was: this is an older version.
    pub replaced: bool,
    /// This Mac's activity has the delete.
    pub by_this_mac: bool,
    /// A folder's files.
    #[serde(default)]
    pub files: Option<u64>,
    /// Who deleted it, from the control plane when this Mac did not.
    #[serde(default)]
    pub by: Option<crate::account::api::PersonRef>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrashKind {
    File,
    Folder,
}

/// An entry `floppy trash restore` put back: where it is now.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct Restored {
    /// The trash entry it was.
    pub id: String,
    pub disk: Option<String>,
    /// Relative to the disk.
    pub path: String,
    pub name: String,
    /// The name was taken: it came back as "name (ripristinato)".
    pub renamed: bool,
    /// Its folder was gone: it came back at the top of the disk.
    pub moved: bool,
}

/// An entry `floppy trash restore` could not put back.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct RestoreFailure {
    pub id: String,
    /// The VM's reason, `not in the trash` for an entry already gone.
    pub error: String,
}

/// What `floppy trash restore` prints: one failed entry does not stop the others.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct RestoreReport {
    pub restored: Vec<Restored>,
    pub failed: Vec<RestoreFailure>,
}

fn parse_json<T: serde::de::DeserializeOwned>(stdout: &str) -> Result<T, EngineError> {
    serde_json::from_str(json_line(stdout)?).map_err(|e| EngineError::Parse(e.to_string()))
}

/// Every mounted disk's trash, from `floppy trash`.
pub fn trash(bin: &Path) -> Result<TrashReport, EngineError> {
    let done = run(bin, &["trash"], TRASH_TIMEOUT)?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    parse_json(&done.stdout)
}

/// Puts trash entries back, never over a file: `floppy trash restore <disk>
/// <id>...`, with `-` for the single-user volume.
pub fn restore(bin: &Path, disk: Option<&str>, ids: &[&str]) -> Result<RestoreReport, EngineError> {
    let mut args = vec!["trash", "restore", disk.unwrap_or("-")];
    args.extend_from_slice(ids);
    let done = run(bin, &args, RESTORE_TIMEOUT)?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    parse_json(&done.stdout)
}

/// `floppy activity` reads the VM's database and asks Samba what is open.
pub const ACTIVITY_TIMEOUT: Duration = Duration::from_secs(20);
/// How many events one `floppy activity` returns at most.
pub const ACTIVITY_PAGE: u64 = 1000;

/// What happened to a file or folder, as the VM records it (DECISIONS #27).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Op {
    /// A folder made.
    Created,
    /// A new file written to the end.
    Added,
    /// An existing file written in place.
    Written,
    /// A save by rename or a Finder "Replace": the old version is in the trash.
    Replaced,
    Renamed,
    Deleted,
}

/// One thing this Mac did on a disk, from `floppy activity`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct LocalEvent {
    /// Increasing in the VM's database: what `--after` takes.
    pub id: u64,
    /// Unix seconds, the VM's clock.
    pub at: f64,
    /// Null for the single-user volume.
    pub disk: Option<String>,
    pub op: Op,
    pub kind: TrashKind,
    /// Relative to the disk.
    pub path: String,
    /// Where a rename or a move took it.
    #[serde(default)]
    pub to: Option<String>,
    /// Bytes, for files; a deleted file's comes from its trash entry.
    #[serde(default)]
    pub size: Option<u64>,
}

/// A file this Mac is writing now.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct Writing {
    pub disk: Option<String>,
    pub path: String,
    /// `added` for a new file arriving, `written` or `replaced` for one in use.
    pub op: Op,
    /// When it was opened, Unix seconds.
    pub since: f64,
    /// Bytes so far; null when it is already gone.
    pub size: Option<u64>,
}

/// A file this Mac has open, read or written. Never sent anywhere.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct OpenFile {
    pub disk: Option<String>,
    pub path: String,
}

/// What `floppy activity` prints.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct ActivityReport {
    pub events: Vec<LocalEvent>,
    /// The last id returned, or `--after` when there was nothing new.
    pub last: u64,
    pub writing: Vec<Writing>,
    pub open: Vec<OpenFile>,
}

/// This Mac's events after `after`, and what it writes and has open now.
pub fn activity(bin: &Path, after: u64) -> Result<ActivityReport, EngineError> {
    let (after, limit) = (after.to_string(), ACTIVITY_PAGE.to_string());
    let done = run(
        bin,
        &["activity", "--after", &after, "--limit", &limit],
        ACTIVITY_TIMEOUT,
    )?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    parse_json(&done.stdout)
}

/// Why a rename could not be undone.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RenameBackRefusal {
    /// Nothing is at the new name any more.
    Gone,
    /// Something else has the old name now.
    Taken,
    /// The folder it was in is gone.
    NoFolder,
}

/// What `floppy activity rename-back` prints.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct RenamedBack {
    pub done: bool,
    pub reason: Option<RenameBackRefusal>,
}

/// Gives `to` its name of before, `path`, never over what holds that name.
/// The paths travel on stdin: names reach the VM through a shell.
pub fn rename_back(
    bin: &Path,
    disk: Option<&str>,
    path: &str,
    to: &str,
) -> Result<RenamedBack, EngineError> {
    let body = serde_json::json!({ "path": path, "to": to }).to_string();
    let done = run_with_input(
        bin,
        &["activity", "rename-back", disk.unwrap_or("-")],
        Some(body.as_bytes()),
        ACTIVITY_TIMEOUT,
    )?;
    if !done.status.success() {
        return Err(failed(&done));
    }
    parse_json(&done.stdout)
}

/// The line that explains a failure: the command's own `floppy: ...` message
/// when there is one, else the last line it printed.
pub fn last_message(stderr: &str) -> Option<String> {
    let lines: Vec<&str> = stderr
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    lines
        .iter()
        .rev()
        .find(|l| l.starts_with("floppy: ") && !l.starts_with("floppy: failed (exit"))
        .or_else(|| lines.last())
        .map(|l| l.trim_start_matches("floppy: ").to_string())
}

/// Streams the lines a running command writes on stderr (where `floppy`
/// writes its messages) and drains stdout, then waits for the end.
pub fn follow(
    mut child: Child,
    mut on_line: impl FnMut(&str),
) -> (Option<ExitStatus>, Vec<String>) {
    let stdout = drain(child.stdout.take());
    let mut tail: Vec<String> = Vec::new();
    if let Some(err) = child.stderr.take() {
        for line in BufReader::new(err).lines().map_while(Result::ok) {
            on_line(&line);
            tail.push(line);
            if tail.len() > 40 {
                tail.remove(0);
            }
        }
    }
    let status = child.wait().ok();
    let _ = stdout.join();
    (status, tail)
}

/// The disk as the app shows it, derived from the report and from what the
/// app itself is running.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DiskState {
    /// No command, or no configuration yet.
    NotInstalled,
    /// Configured but `floppy setup` never completed.
    NeedsSetup,
    /// Turned off by the user (`floppy stop`); it does not come back by itself.
    Off,
    Starting,
    Stopping,
    Connected,
    Unreachable,
    /// Team version, ready, with no disk to mount: not signed in yet, no disk
    /// given by the team, or every disk turned off by the person.
    NoDisks,
}

/// Why the disk is unreachable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Problem {
    VmStopped,
    VmBroken,
    DriveDown,
    NotMounted,
    NotResponding,
    Engine,
}

/// What the app has running, which changes how the state reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobKind {
    /// The bundled engine copied into the engine's home, then `floppy setup`.
    Install,
    Setup,
    Start,
    Stop,
    /// `floppy stop` then `floppy start`: the VM takes the memory its disks want.
    Restart,
    Warmup,
    Free,
}

/// The state the app shows for a report and the job it is running.
pub fn derive(
    report: &Result<EngineReport, EngineError>,
    job: Option<JobKind>,
) -> (DiskState, Option<Problem>) {
    match job {
        Some(JobKind::Install | JobKind::Setup | JobKind::Start | JobKind::Restart) => {
            return (DiskState::Starting, None);
        }
        Some(JobKind::Stop) => return (DiskState::Stopping, None),
        _ => {}
    }
    let r = match report {
        Ok(r) => r,
        Err(EngineError::Missing(_)) => return (DiskState::NotInstalled, None),
        Err(_) => return (DiskState::Unreachable, Some(Problem::Engine)),
    };
    if r.mode == Mode::Team {
        return derive_team(r);
    }
    if !r.configured {
        return (DiskState::NotInstalled, None);
    }
    if !r.installed {
        return (DiskState::NeedsSetup, None);
    }
    if r.stopped {
        return (DiskState::Off, None);
    }
    let problem = match r.vm {
        VmState::Missing | VmState::Broken => Some(Problem::VmBroken),
        VmState::Stopped | VmState::Other => Some(Problem::VmStopped),
        VmState::Running if r.drive.as_deref() != Some("active") => Some(Problem::DriveDown),
        VmState::Running if r.mount_point.is_none() => Some(Problem::NotMounted),
        VmState::Running if r.responding == Some(false) => Some(Problem::NotResponding),
        VmState::Running => None,
    };
    match problem {
        Some(p) => (DiskState::Unreachable, Some(p)),
        None => (DiskState::Connected, None),
    }
}

/// The team version: the faceplate is the share at /Volumes/Floppy, and a
/// disk that is starting, failed or waiting shows on its own row instead of
/// making the whole Floppy unreachable.
fn derive_team(r: &EngineReport) -> (DiskState, Option<Problem>) {
    if !r.installed {
        return (DiskState::NeedsSetup, None);
    }
    if r.stopped {
        return (DiskState::Off, None);
    }
    match r.vm {
        VmState::Missing | VmState::Broken => {
            return (DiskState::Unreachable, Some(Problem::VmBroken));
        }
        VmState::Stopped | VmState::Other => {
            return (DiskState::Unreachable, Some(Problem::VmStopped));
        }
        VmState::Running => {}
    }
    let enabled: Vec<&EngineDisk> = r.team_disks().iter().filter(|d| d.enabled).collect();
    if !r.configured || enabled.is_empty() {
        return (DiskState::NoDisks, None);
    }
    // `drive` is Samba's state, or failed/activating for the disks: a disk
    // that explains it is shown on its row.
    let disks_explain = enabled.iter().any(|d| d.state != RunState::Mounted);
    let problem = if r.drive.as_deref() != Some("active") && !disks_explain {
        Some(Problem::DriveDown)
    } else if r.mount_point.is_none() {
        Some(Problem::NotMounted)
    } else if r.responding == Some(false) {
        Some(Problem::NotResponding)
    } else {
        None
    };
    match problem {
        Some(p) => (DiskState::Unreachable, Some(p)),
        None => (DiskState::Connected, None),
    }
}

/// Space on the Mac, with the command's two thresholds: below the reserve the
/// panel warns, below half of it the agent frees the whole cache (`tick`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Space {
    pub cache_used_mb: Option<u64>,
    pub cache_limit_mb: Option<u64>,
    pub mac_free_mb: Option<u64>,
    pub reserve_mb: u64,
    pub low: bool,
}

/// The space figures of a report, with the command's default reserve.
pub fn space(report: &EngineReport) -> Space {
    let reserve_mb = report.reserve_mb.unwrap_or(DEFAULT_RESERVE_MB);
    Space {
        cache_used_mb: report.cache_used_mb,
        cache_limit_mb: report.cache_limit_mb,
        mac_free_mb: report.mac_free_mb,
        reserve_mb,
        low: report.mac_free_mb.is_some_and(|f| f < reserve_mb),
    }
}

/// True when `path` is the mount point or inside it. Paths are compared as
/// given: the command resolves them again before mapping them into the VM.
pub fn is_on_disk(path: &Path, mount_point: &str) -> bool {
    let mp = Path::new(mount_point);
    path.is_absolute()
        && path.starts_with(mp)
        && !path
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::io::Write;

    pub fn report() -> EngineReport {
        EngineReport {
            version: 1,
            configured: true,
            installed: true,
            stopped: false,
            vm: VmState::Running,
            mount_point: Some("/Volumes/Floppy".into()),
            responding: Some(true),
            drive: Some("active".into()),
            cache_used_mb: Some(48213),
            cache_limit_mb: Some(102400),
            mac_free_mb: Some(201975),
            reserve_mb: Some(20480),
            mode: Mode::Single,
            vm_memory_mib: None,
            vm_memory_wanted_mib: None,
            disks: None,
        }
    }

    /// A disk line as the engine prints it (docs/ARCHITECTURE.md, "Team disks").
    pub const DISK_A: &str = r#"{"id":"0192-a","folder":"Archivio 2025","access":"read","enabled":true,"state":"mounted","mounted_access":"read","pending":null,"cache_used_mb":812,"cache_size_mb":10240,"replica":"up","mode":"replica","online":true}"#;

    /// `status --json` of the team version with `disks` (each a JSON object).
    pub fn team_status(disks: &[&str], extra: &str) -> String {
        format!(
            r#"{{"version":1,"mode":"team","configured":true,"installed":true,"stopped":false,"vm":"running","mount_point":"/Volumes/Floppy","responding":true,"drive":"active","cache_used_mb":812,"cache_limit_mb":30720,"mac_free_mb":201975,"reserve_mb":20480,"vm_memory_mib":1024,"vm_memory_wanted_mib":1024,"disks":[{}]{extra}}}"#,
            disks.join(",")
        )
    }

    /// The disk line with some fields replaced (`"key":value` pairs).
    pub fn disk_with(base: &str, changes: &[(&str, &str)]) -> String {
        let mut v: serde_json::Value = serde_json::from_str(base).unwrap();
        for (k, val) in changes {
            v[*k] = serde_json::from_str(val).unwrap();
        }
        v.to_string()
    }

    /// A fake `floppy` in a temp dir, so nothing here touches ~/.floppy.
    pub fn fake(dir: &Path, body: &str) -> PathBuf {
        let path = dir.join("floppy");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(f, "#!/bin/bash\n{body}").unwrap();
        drop(f);
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    #[test]
    fn parses_the_command_output() {
        let out = r#"{"version":1,"configured":true,"installed":true,"stopped":false,"vm":"running","mount_point":"/Volumes/Floppy","responding":true,"drive":"active","cache_used_mb":48213,"cache_limit_mb":102400,"mac_free_mb":201975,"reserve_mb":20480}"#;
        assert_eq!(parse_report(out).unwrap(), report());
    }

    #[test]
    fn parses_the_output_with_no_install() {
        let out = r#"{"version":1,"configured":false,"installed":false,"stopped":false,"vm":"missing","mount_point":null,"responding":null,"drive":null,"cache_used_mb":null,"cache_limit_mb":null,"mac_free_mb":201975,"reserve_mb":null}"#;
        let r = parse_report(out).unwrap();
        assert_eq!(r.vm, VmState::Missing);
        assert_eq!(derive(&Ok(r), None), (DiskState::NotInstalled, None));
    }

    #[test]
    fn parses_the_team_status() {
        let out = team_status(&[DISK_A], "");
        let r = parse_report(&out).unwrap();
        assert_eq!(r.mode, Mode::Team);
        let d = &r.team_disks()[0];
        assert_eq!(d.folder, "Archivio 2025");
        assert_eq!(
            (d.access, d.mounted_access),
            (Access::Read, Some(Access::Read))
        );
        assert_eq!(
            (d.state, d.replica, d.mode),
            (RunState::Mounted, Replica::Up, Some(MetaMode::Replica))
        );
        assert_eq!(
            (d.cache_used_mb, d.cache_size_mb, d.online),
            (Some(812), Some(10240), Some(true))
        );
        assert!(!r.restart_needed());
        assert_eq!(derive(&Ok(r), None), (DiskState::Connected, None));

        // Values a newer engine may add read as `other`, not as an error.
        let newer = disk_with(
            DISK_A,
            &[
                ("state", "\"paused\""),
                ("replica", "\"x\""),
                ("mode", "\"y\""),
                ("pending", "\"z\""),
            ],
        );
        let r = parse_report(&team_status(&[&newer], "")).unwrap();
        let d = &r.team_disks()[0];
        assert_eq!(
            (d.state, d.replica, d.mode, d.pending),
            (
                RunState::Other,
                Replica::Other,
                Some(MetaMode::Other),
                Some(Pending::Other)
            )
        );

        // The VM not running: disks null.
        let stopped = r#"{"version":1,"mode":"team","configured":true,"installed":true,"stopped":true,"vm":"stopped","mount_point":null,"responding":null,"drive":null,"cache_used_mb":null,"cache_limit_mb":null,"mac_free_mb":1000,"reserve_mb":20480,"vm_memory_mib":1024,"vm_memory_wanted_mib":1280,"disks":null}"#;
        let r = parse_report(stopped).unwrap();
        assert!(r.team_disks().is_empty());
        assert!(r.restart_needed());
        assert_eq!(derive(&Ok(r), None).0, DiskState::Off);
    }

    #[test]
    fn parses_lag_and_unlisted_disks() {
        let gone = disk_with(
            DISK_A,
            &[
                ("listed", "false"),
                ("enabled", "false"),
                ("pending", "\"remove\""),
                ("lag_ms", "512"),
            ],
        );
        let r = parse_report(&team_status(&[DISK_A, &gone], "")).unwrap();
        let (a, b) = (&r.team_disks()[0], &r.team_disks()[1]);
        assert!(a.listed, "an engine without the field lists the disk");
        assert_eq!(a.lag_ms, None);
        assert!(!b.listed);
        assert_eq!((b.lag_ms, b.pending), (Some(512), Some(Pending::Remove)));
        // A disk on its way out is not one the person still uses.
        assert_eq!(derive(&Ok(r), None).0, DiskState::Connected);
    }

    #[test]
    fn derives_the_team_states() {
        let state = |out: String| derive(&parse_report(&out), None);
        // Before sign-in: the VM is ready, no disks file.
        let fresh = team_status(&[], "").replace(r#""configured":true"#, r#""configured":false"#);
        assert_eq!(state(fresh), (DiskState::NoDisks, None));
        // Every disk turned off.
        let off = disk_with(DISK_A, &[("enabled", "false"), ("state", "\"disabled\"")]);
        assert_eq!(state(team_status(&[&off], "")).0, DiskState::NoDisks);
        // A disk failed or starting is its row's business, not the whole Floppy.
        let failed = disk_with(DISK_A, &[("state", "\"failed\"")]);
        let both = team_status(&[DISK_A, &failed], "")
            .replace(r#""drive":"active""#, r#""drive":"failed""#);
        assert_eq!(state(both), (DiskState::Connected, None));
        // Samba down with every disk mounted is the drive.
        let samba =
            team_status(&[DISK_A], "").replace(r#""drive":"active""#, r#""drive":"inactive""#);
        assert_eq!(
            state(samba),
            (DiskState::Unreachable, Some(Problem::DriveDown))
        );
        let unmounted = team_status(&[DISK_A], "").replace(
            r#""mount_point":"/Volumes/Floppy""#,
            r#""mount_point":null"#,
        );
        assert_eq!(
            state(unmounted),
            (DiskState::Unreachable, Some(Problem::NotMounted))
        );
        let setup = team_status(&[], "").replace(r#""installed":true"#, r#""installed":false"#);
        assert_eq!(state(setup).0, DiskState::NeedsSetup);
        let broken = team_status(&[DISK_A], "").replace(r#""vm":"running""#, r#""vm":"broken""#);
        assert_eq!(
            state(broken),
            (DiskState::Unreachable, Some(Problem::VmBroken))
        );
        assert_eq!(
            derive(&Ok(report()), Some(JobKind::Restart)).0,
            DiskState::Starting
        );
    }

    #[test]
    fn apply_sends_the_secrets_on_stdin_only() {
        let dir = tempfile::tempdir().unwrap();
        let seen = dir.path().join("seen");
        let answer = format!(
            r#"{{"version":1,"mount_point":"/Volumes/Floppy","cache_per_disk_mb":10240,"vm_memory_mib":1024,"vm_memory_wanted_mib":1280,"restart_needed":true,"vm":{{"smbd":"active","drive":"active","cache_used_mb":1,"cache_limit_mb":30720,"disks":[{DISK_A}]}}}}"#
        );
        let bin = fake(
            dir.path(),
            &format!(
                r#"[ "$1 $2" = "disks apply" ] || exit 2
{{ echo "args: $*"; echo "env: $(env | grep -c pw-secret)"; printf 'stdin: '; cat; echo; }} > "{}"
echo "Starting the VM..." >&2
echo '{answer}'"#,
                seen.display()
            ),
        );
        let report = apply(&bin, Some(br#"{"version":1,"meta_password":"pw-secret"}"#)).unwrap();
        assert!(report.restart_needed);
        assert_eq!(report.vm.disks[0].id, "0192-a");
        let seen = std::fs::read_to_string(&seen).unwrap();
        assert_eq!(
            seen,
            "args: disks apply\nenv: 0\nstdin: {\"version\":1,\"meta_password\":\"pw-secret\"}\n"
        );

        // Without secrets the stdin is empty, not the app's own.
        let bin = fake(
            dir.path(),
            &format!(r#"[ -z "$(cat)" ] || exit 3; echo '{answer}'"#),
        );
        assert!(apply(&bin, None).is_ok());
    }

    #[test]
    fn apply_and_clear_report_the_engine_message() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            "cat >/dev/null; echo 'floppy: this Mac runs the single-user volume (config.env): team disks need the team install' >&2; exit 1",
        );
        assert_eq!(
            apply(&bin, None).unwrap_err(),
            EngineError::Failed(
                "this Mac runs the single-user volume (config.env): team disks need the team install".into()
            )
        );
        let bin = fake(
            dir.path(),
            r#"[ "$1 $2" = "disks clear" ] && echo '{"version":1,"cleared":true}'"#,
        );
        assert_eq!(clear(&bin), Ok(()));
        assert!(matches!(
            clear(&dir.path().join("none")),
            Err(EngineError::Missing(_))
        ));
        assert!(parse_apply(r#"{"version":2}"#).is_err());
    }

    #[test]
    fn rejects_an_unknown_version_and_garbage() {
        assert!(parse_report(r#"{"version":2}"#).is_err());
        assert!(parse_report("VM: Running").is_err());
    }

    #[test]
    fn unknown_vm_states_are_other() {
        let r: VmState = serde_json::from_str("\"paused\"").unwrap();
        assert_eq!(r, VmState::Other);
    }

    #[test]
    fn derives_each_state() {
        let ok = report();
        assert_eq!(derive(&Ok(ok.clone()), None), (DiskState::Connected, None));
        let off = EngineReport {
            stopped: true,
            ..ok.clone()
        };
        assert_eq!(derive(&Ok(off), None).0, DiskState::Off);
        let setup = EngineReport {
            installed: false,
            ..ok.clone()
        };
        assert_eq!(derive(&Ok(setup), None).0, DiskState::NeedsSetup);
        let cases = [
            (
                EngineReport {
                    vm: VmState::Stopped,
                    ..ok.clone()
                },
                Problem::VmStopped,
            ),
            (
                EngineReport {
                    vm: VmState::Broken,
                    ..ok.clone()
                },
                Problem::VmBroken,
            ),
            (
                EngineReport {
                    drive: Some("failed".into()),
                    ..ok.clone()
                },
                Problem::DriveDown,
            ),
            (
                EngineReport {
                    mount_point: None,
                    ..ok.clone()
                },
                Problem::NotMounted,
            ),
            (
                EngineReport {
                    responding: Some(false),
                    ..ok.clone()
                },
                Problem::NotResponding,
            ),
        ];
        for (r, p) in cases {
            assert_eq!(derive(&Ok(r), None), (DiskState::Unreachable, Some(p)));
        }
        let missing = Err(EngineError::Missing("/x".into()));
        assert_eq!(derive(&missing, None).0, DiskState::NotInstalled);
        let hung = Err(EngineError::Timeout(25));
        assert_eq!(
            derive(&hung, None),
            (DiskState::Unreachable, Some(Problem::Engine))
        );
    }

    #[test]
    fn a_running_job_overrides_the_report() {
        let ok = Ok(report());
        assert_eq!(derive(&ok, Some(JobKind::Start)).0, DiskState::Starting);
        assert_eq!(derive(&ok, Some(JobKind::Stop)).0, DiskState::Stopping);
        assert_eq!(derive(&ok, Some(JobKind::Warmup)).0, DiskState::Connected);
    }

    #[test]
    fn space_warns_below_the_reserve() {
        let r = EngineReport {
            mac_free_mb: Some(12000),
            reserve_mb: None,
            ..report()
        };
        let s = space(&r);
        assert_eq!(s.reserve_mb, DEFAULT_RESERVE_MB);
        assert!(s.low);
        assert!(!space(&report()).low);
    }

    #[test]
    fn only_paths_on_the_disk_are_accepted() {
        let mp = "/Volumes/Floppy";
        assert!(is_on_disk(Path::new("/Volumes/Floppy"), mp));
        assert!(is_on_disk(Path::new("/Volumes/Floppy/Showreel 2026"), mp));
        assert!(!is_on_disk(Path::new("/Volumes/Floppy-1/x"), mp));
        assert!(!is_on_disk(
            Path::new("/Volumes/Floppy/../Macintosh HD"),
            mp
        ));
        assert!(!is_on_disk(Path::new("Floppy/x"), mp));
    }

    #[test]
    fn last_message_prefers_the_command_message() {
        let err = "Starting the VM...\nfloppy: not enough free space on this Mac: 3 GB free\nfloppy: failed (exit 1, line 262). Last lines of x:\nnoise\n";
        assert_eq!(
            last_message(err).unwrap(),
            "not enough free space on this Mac: 3 GB free"
        );
        assert_eq!(last_message("just this\n").unwrap(), "just this");
        assert_eq!(last_message(""), None);
    }

    #[test]
    fn status_runs_a_fake_command() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            r#"[ "$1 $2" = "status --json" ] || exit 2
echo '{"version":1,"configured":true,"installed":true,"stopped":true,"vm":"stopped","mount_point":null,"responding":null,"drive":null,"cache_used_mb":null,"cache_limit_mb":null,"mac_free_mb":1000,"reserve_mb":null}'"#,
        );
        let r = status(&bin).unwrap();
        assert!(r.stopped);
        assert_eq!(derive(&Ok(r), None).0, DiskState::Off);
    }

    #[test]
    fn a_missing_command_is_not_installed() {
        let dir = tempfile::tempdir().unwrap();
        let err = status(&dir.path().join("floppy")).unwrap_err();
        assert!(matches!(err, EngineError::Missing(_)));
    }

    #[test]
    fn a_failing_command_reports_its_message() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            "echo 'floppy: the VM is not responding' >&2; exit 1",
        );
        assert_eq!(
            status(&bin).unwrap_err(),
            EngineError::Failed("the VM is not responding".into())
        );
    }

    const TRASH: &str = r#"{"disks": [{"disk": "progetti", "folder": "Progetti", "writable": true, "trash_days": 7, "entries": [{"id": "2026-10-19-07/2050-2051", "name": "Girato", "kind": "folder", "from": "Cantine", "deleted_at": 1792395950.3, "expires_at": 1792998000.0, "size": 4, "replaced": false, "by_this_mac": true, "files": 2}, {"id": "2026-10-19-07/2055-2060", "name": "musica.xlsx", "kind": "file", "from": null, "deleted_at": 1792396089.6, "expires_at": 1792998000.0, "size": 3, "replaced": true, "by_this_mac": false}]}]}"#;

    #[test]
    fn reads_the_trash() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(dir.path(), &format!("[ \"$*\" = trash ] && echo '{TRASH}'"));
        let t = trash(&bin).unwrap();
        let d = &t.disks[0];
        assert_eq!(
            (d.disk.as_deref(), d.writable, d.trash_days),
            (Some("progetti"), true, 7)
        );
        assert_eq!(d.entries[0].kind, TrashKind::Folder);
        assert_eq!(d.entries[0].files, Some(2));
        assert_eq!(d.entries[1].from, None);
        assert_eq!(d.entries[1].files, None);
    }

    #[test]
    fn restores_with_the_disk_or_a_dash() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            r#"[ "$1 $2" = "trash restore" ] || exit 2
echo "{\"restored\": [{\"id\": \"$4\", \"disk\": $([ "$3" = - ] && echo null || echo "\"$3\""), \"path\": \"Cantine/$4\", \"name\": \"x\", \"renamed\": true, \"moved\": false}], \"failed\": [{\"id\": \"$5\", \"error\": \"not in the trash\"}]}""#,
        );
        let r = restore(&bin, Some("progetti"), &["2026-10-19-07/1-2", "h/3-4"]).unwrap();
        assert_eq!(r.restored[0].disk.as_deref(), Some("progetti"));
        assert_eq!(r.restored[0].path, "Cantine/2026-10-19-07/1-2");
        assert_eq!(r.failed[0].id, "h/3-4");
        let single = restore(&bin, None, &["h/1-2"]).unwrap();
        assert_eq!(single.restored[0].disk, None);
    }

    #[test]
    fn reads_the_activity() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            r#"[ "$*" = "activity --after 7 --limit 1000" ] || exit 2
echo '{"events": [{"id": 8, "at": 1792395950.25, "disk": "progetti", "op": "renamed", "kind": "folder", "path": "Backstage", "to": "Backstage Tosca"}, {"id": 9, "at": 1792395951.0, "disk": null, "op": "deleted", "kind": "file", "path": "a.mov", "size": 12}], "last": 9, "writing": [{"disk": "progetti", "path": "Girato/c.mov", "op": "added", "since": 1792395900.0, "size": null}], "open": [{"disk": "progetti", "path": "Girato/d.mov"}]}'"#,
        );
        let a = activity(&bin, 7).unwrap();
        assert_eq!(a.last, 9);
        assert_eq!(a.events[0].op, Op::Renamed);
        assert_eq!(a.events[0].to.as_deref(), Some("Backstage Tosca"));
        assert_eq!(
            (a.events[1].disk.as_deref(), a.events[1].size),
            (None, Some(12))
        );
        assert_eq!(a.writing[0].op, Op::Added);
        assert_eq!(a.open[0].path, "Girato/d.mov");
    }

    #[test]
    fn renames_back_with_the_paths_on_stdin() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            r#"[ "$*" = "activity rename-back progetti" ] || exit 2
[ "$(cat)" = '{"path":"Backstage","to":"Back'"'"'stage Tosca"}' ] || exit 3
echo '{"disk": "progetti", "path": "Backstage", "to": "x", "done": false, "reason": "taken"}'"#,
        );
        let r = rename_back(&bin, Some("progetti"), "Backstage", "Back'stage Tosca").unwrap();
        assert_eq!((r.done, r.reason), (false, Some(RenameBackRefusal::Taken)));
    }

    #[test]
    fn a_failed_restore_says_why() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            "echo 'floppy-activity: the disk is read-only on this Mac: x' >&2; exit 1",
        );
        assert_eq!(
            restore(&bin, Some("archivio"), &["h/1-2"]).unwrap_err(),
            EngineError::Failed("floppy-activity: the disk is read-only on this Mac: x".into())
        );
    }

    #[test]
    fn a_hung_command_is_killed_with_its_children() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(dir.path(), "sleep 30 & wait");
        let started = Instant::now();
        let err = run(&bin, &["status", "--json"], Duration::from_millis(300)).unwrap_err();
        assert_eq!(err, EngineError::Timeout(0));
        // The background sleep held stdout open: returning at all means the
        // whole group was killed.
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn follow_streams_stderr_lines() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(dir.path(), "echo one >&2; echo out; echo two >&2; exit 3");
        let child = spawn(&bin, &["setup"]).unwrap();
        let mut seen = Vec::new();
        let (status, tail) = follow(child, |l| seen.push(l.to_string()));
        assert_eq!(seen, ["one", "two"]);
        assert_eq!(tail, ["one", "two"]);
        assert_eq!(status.unwrap().code(), Some(3));
    }
}
