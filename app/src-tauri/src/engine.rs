//! The engine is the `floppy` command (`macos/bin/floppy`, installed in
//! `~/.floppy/bin/floppy` from the app's bundle, see `installer`). The app never touches the VM, the mount, launchd
//! or `nsmb.conf` itself: it runs the command and reads what it answers.
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

/// What `floppy status --json` prints (version 1).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct EngineReport {
    pub version: u32,
    pub configured: bool,
    pub installed: bool,
    pub stopped: bool,
    pub vm: VmState,
    pub mount_point: Option<String>,
    pub responding: Option<bool>,
    pub drive: Option<String>,
    pub cache_used_mb: Option<u64>,
    pub cache_limit_mb: Option<u64>,
    pub mac_free_mb: Option<u64>,
    pub reserve_mb: Option<u64>,
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

/// The report in the command's output: the last line that holds a JSON object.
pub fn parse_report(stdout: &str) -> Result<EngineReport, EngineError> {
    let line = stdout
        .lines()
        .rev()
        .find(|l| l.trim_start().starts_with('{'))
        .ok_or_else(|| EngineError::Parse("no JSON object in the output".into()))?;
    let report: EngineReport =
        serde_json::from_str(line).map_err(|e| EngineError::Parse(e.to_string()))?;
    if report.version != 1 {
        return Err(EngineError::Parse(format!(
            "status version {} is not supported",
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
    let mut child = spawn(bin, args)?;
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
        return Err(EngineError::Failed(
            last_message(&done.stderr)
                .unwrap_or_else(|| format!("exit {}", done.status.code().unwrap_or(-1))),
        ));
    }
    parse_report(&done.stdout)
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
    Warmup,
    Free,
}

/// The state the app shows for a report and the job it is running.
pub fn derive(
    report: &Result<EngineReport, EngineError>,
    job: Option<JobKind>,
) -> (DiskState, Option<Problem>) {
    match job {
        Some(JobKind::Install | JobKind::Setup | JobKind::Start) => {
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
        }
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
