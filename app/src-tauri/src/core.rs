//! The app's state: the last report from the engine, the job the app is
//! running (one at a time), and the periodic check. Events go out through a
//! callback, so this module has no Tauri dependency and is tested with a
//! fake `floppy` command.

use crate::engine::{self, DiskState, EngineError, EngineReport, JobKind, Problem, Space};
use crate::installer::{InstallNeed, Installer};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// How often the engine is checked while the disk works.
pub const CHECK_INTERVAL: Duration = Duration::from_secs(15);
/// How often it is checked while unreachable (DECISIONS #12: every 30 s).
pub const RETRY_INTERVAL: Duration = Duration::from_secs(30);
/// Reading the disk's top folders gives up after this: a dead SMB mount can
/// hang whoever lists it.
pub const LIST_TIMEOUT: Duration = Duration::from_secs(3);

/// The job the app is running.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct JobInfo {
    pub kind: JobKind,
    pub path: Option<String>,
    /// The folder's name, for the panel and the notification.
    pub name: Option<String>,
    pub started_ms: u64,
}

/// A job that ended badly, until the next one of the same kind succeeds or
/// the user dismisses it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct JobError {
    pub kind: JobKind,
    pub name: Option<String>,
    pub message: String,
}

/// Everything the panel, the windows and the tray icon show.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DiskStatus {
    pub state: DiskState,
    pub problem: Option<Problem>,
    pub mount_point: Option<String>,
    /// Mounted somewhere other than /Volumes/Floppy: CapCut links break.
    pub wrong_mount_point: bool,
    pub space: Option<Space>,
    pub job: Option<JobInfo>,
    pub last_error: Option<JobError>,
    /// Engine message when the command itself failed.
    pub engine_error: Option<String>,
    /// Seconds from `checked_ms` to the next check.
    pub next_check_s: u64,
    pub checked_ms: u64,
    pub engine_path: String,
    /// What this Mac needs from the engine in the app's bundle, if anything.
    pub install: Option<InstallNeed>,
    /// The app carries an engine: a missing command can be installed.
    pub bundled: bool,
}

/// Where the install and `floppy setup` are, in the four steps the first-run
/// window shows.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct SetupProgress {
    pub running: bool,
    /// 0 copy the engine, 1 virtual disk, 2 start, 3 connect the disk; 4
    /// when done. A setup alone starts at 1.
    pub step: u8,
    pub done: bool,
    pub error: Option<SetupError>,
}

/// Why `floppy setup` stopped.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SetupError {
    /// `space` when the Mac has too little free space, else `other`.
    pub kind: &'static str,
    pub message: String,
}

/// A notification the app should show, rendered by the frontend dictionary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Notice {
    Reconnected,
    WarmupDone { name: String },
}

/// What the core tells the app shell.
#[derive(Debug, Clone)]
pub enum Event {
    Status(Box<DiskStatus>),
    Setup(SetupProgress),
    Notify(Notice),
}

/// A folder at the top of the disk: a project.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Folder {
    pub name: String,
    pub path: String,
}

/// Why a command from the UI was refused or failed.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum CoreError {
    #[error("another operation is running")]
    Busy,
    #[error("the disk is not mounted")]
    NotMounted,
    #[error("{0} is not on the disk")]
    NotOnDisk(String),
    #[error("nothing to stop")]
    NothingToCancel,
    #[error("listing the disk did not answer in time")]
    ListTimeout,
    #[error("this build of the app carries no engine")]
    NoBundle,
    #[error(transparent)]
    Engine(#[from] EngineError),
}

struct Running {
    info: JobInfo,
    pid: u32,
    cancelled: bool,
}

struct Inner {
    report: Option<Result<EngineReport, EngineError>>,
    job: Option<Running>,
    last_error: Option<JobError>,
    next_check: Duration,
    checked_ms: u64,
    shown: Option<DiskState>,
    setup: SetupProgress,
    install: Option<InstallNeed>,
}

type Emit = Box<dyn Fn(Event) + Send + Sync>;

/// The shared state, owned by an `Arc` and used from Tauri commands, the
/// checking thread and the job threads.
pub struct Core {
    engine: PathBuf,
    installer: Option<Installer>,
    inner: Mutex<Inner>,
    emit: Emit,
    wake: Mutex<Sender<()>>,
    wake_rx: Mutex<Option<Receiver<()>>>,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn folder_name(path: &str) -> Option<String> {
    Path::new(path)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
}

impl Core {
    /// A core that runs `engine`, installs it from `installer` when the app
    /// carries one, and reports through `emit`.
    pub fn new(
        engine: PathBuf,
        installer: Option<Installer>,
        emit: impl Fn(Event) + Send + Sync + 'static,
    ) -> Arc<Self> {
        let (tx, rx) = mpsc::channel();
        let install = installer.as_ref().and_then(Installer::need);
        Arc::new(Core {
            engine,
            installer,
            inner: Mutex::new(Inner {
                report: None,
                job: None,
                last_error: None,
                next_check: CHECK_INTERVAL,
                checked_ms: 0,
                shown: None,
                setup: SetupProgress::default(),
                install,
            }),
            emit: Box::new(emit),
            wake: Mutex::new(tx),
            wake_rx: Mutex::new(Some(rx)),
        })
    }

    /// The path of the `floppy` command this core runs.
    pub fn engine(&self) -> &Path {
        &self.engine
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        // A panic in another thread must not take the tray icon down with it.
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The current status, from the last check.
    pub fn status(&self) -> DiskStatus {
        let inner = self.lock();
        self.compose(&inner)
    }

    /// Where `floppy setup` is.
    pub fn setup(&self) -> SetupProgress {
        self.lock().setup.clone()
    }

    fn compose(&self, inner: &Inner) -> DiskStatus {
        let job_kind = inner.job.as_ref().map(|j| j.info.kind);
        let pending = Err(EngineError::Timeout(0));
        let report = inner.report.as_ref().unwrap_or(&pending);
        let (mut state, problem) = engine::derive(report, job_kind);
        // Before the first answer the app is starting, not broken.
        if inner.report.is_none() && job_kind.is_none() {
            state = DiskState::Starting;
        }
        let ok = report.as_ref().ok();
        let mount_point = ok.and_then(|r| r.mount_point.clone());
        DiskStatus {
            state,
            problem: if inner.report.is_none() {
                None
            } else {
                problem
            },
            wrong_mount_point: mount_point
                .as_deref()
                .is_some_and(|m| m != engine::EXPECTED_MOUNT_POINT),
            mount_point,
            space: ok.filter(|r| r.configured).map(engine::space),
            job: inner.job.as_ref().map(|j| j.info.clone()),
            last_error: inner.last_error.clone(),
            engine_error: match report {
                Err(e) if inner.report.is_some() && !matches!(e, EngineError::Missing(_)) => {
                    Some(e.to_string())
                }
                _ => None,
            },
            next_check_s: inner.next_check.as_secs(),
            checked_ms: inner.checked_ms,
            engine_path: self.engine.display().to_string(),
            install: inner.install,
            bundled: self.installer.is_some(),
        }
    }

    fn publish(&self, inner: &mut Inner) {
        let status = self.compose(inner);
        // The disk came back by itself (the agent remounted it): say so once.
        if inner.shown == Some(DiskState::Unreachable) && status.state == DiskState::Connected {
            (self.emit)(Event::Notify(Notice::Reconnected));
        }
        inner.shown = Some(status.state);
        (self.emit)(Event::Status(Box::new(status)));
    }

    /// Runs `floppy status --json` now and publishes the result.
    pub fn refresh(&self) {
        let report = engine::status(&self.engine);
        let install = self.installer.as_ref().and_then(Installer::need);
        let mut inner = self.lock();
        // While the install runs the home is half copied: keep what it was.
        if inner
            .job
            .as_ref()
            .is_none_or(|j| j.info.kind != JobKind::Install)
        {
            inner.install = install;
        }
        let (state, _) = engine::derive(&report, inner.job.as_ref().map(|j| j.info.kind));
        inner.next_check = if state == DiskState::Unreachable {
            RETRY_INTERVAL
        } else {
            CHECK_INTERVAL
        };
        inner.report = Some(report);
        inner.checked_ms = now_ms();
        self.publish(&mut inner);
    }

    /// Asks the checking thread for a check now.
    pub fn wake(&self) {
        if let Ok(tx) = self.wake.lock() {
            let _ = tx.send(());
        }
    }

    /// Starts the thread that checks the engine. Call once.
    pub fn start_checking(self: &Arc<Self>) {
        let Some(rx) = self.wake_rx.lock().ok().and_then(|mut r| r.take()) else {
            return;
        };
        let core = Arc::clone(self);
        thread::spawn(move || {
            loop {
                core.refresh();
                let wait = core.lock().next_check;
                match rx.recv_timeout(wait) {
                    Ok(()) => while rx.try_recv().is_ok() {},
                    Err(RecvTimeoutError::Timeout) => {}
                    Err(RecvTimeoutError::Disconnected) => break,
                }
            }
        });
    }

    fn mount_point(&self) -> Option<String> {
        let inner = self.lock();
        inner
            .report
            .as_ref()
            .and_then(|r| r.as_ref().ok())
            .and_then(|r| r.mount_point.clone())
    }

    /// Checks that `path` is on the mounted disk, for warmup, free and Finder.
    pub fn disk_path(&self, path: &str) -> Result<String, CoreError> {
        let mp = self.mount_point().ok_or(CoreError::NotMounted)?;
        if engine::is_on_disk(Path::new(path), &mp) {
            Ok(path.to_string())
        } else {
            Err(CoreError::NotOnDisk(path.to_string()))
        }
    }

    /// The folders at the top of the disk, alphabetically. Read on a thread
    /// that is abandoned after `LIST_TIMEOUT`: a dead mount blocks readdir.
    pub fn folders(&self) -> Result<Vec<Folder>, CoreError> {
        let mp = self.mount_point().ok_or(CoreError::NotMounted)?;
        list_folders(Path::new(&mp), LIST_TIMEOUT)
    }

    /// Starts a job: the bundled install, `floppy start|stop|setup`, or
    /// `warmup|free <path>`.
    pub fn run_job(self: &Arc<Self>, kind: JobKind, path: Option<String>) -> Result<(), CoreError> {
        let mut args: Vec<&str> = vec![match kind {
            JobKind::Install => "install",
            JobKind::Setup => "setup",
            JobKind::Start => "start",
            JobKind::Stop => "stop",
            JobKind::Warmup => "warmup",
            JobKind::Free => "free",
        }];
        if let Some(p) = path.as_deref() {
            args.push(p);
        }
        let mut inner = self.lock();
        if inner.job.is_some() {
            return Err(CoreError::Busy);
        }
        let child = match kind {
            JobKind::Install => self
                .installer
                .as_ref()
                .ok_or(CoreError::NoBundle)?
                .spawn()?,
            _ => engine::spawn(&self.engine, &args)?,
        };
        let info = JobInfo {
            kind,
            name: path.as_deref().and_then(folder_name),
            path: path.clone(),
            started_ms: now_ms(),
        };
        inner.job = Some(Running {
            info: info.clone(),
            pid: child.id(),
            cancelled: false,
        });
        if inner.last_error.as_ref().is_some_and(|e| e.kind == kind) {
            inner.last_error = None;
        }
        if matches!(kind, JobKind::Install | JobKind::Setup) {
            inner.setup = SetupProgress {
                running: true,
                step: if kind == JobKind::Install { 0 } else { 1 },
                ..SetupProgress::default()
            };
            (self.emit)(Event::Setup(inner.setup.clone()));
        }
        self.publish(&mut inner);
        drop(inner);

        let core = Arc::clone(self);
        thread::spawn(move || {
            let (status, tail) = engine::follow(child, |line| {
                if matches!(kind, JobKind::Install | JobKind::Setup) {
                    core.setup_line(line);
                }
            });
            core.finish(info, status.is_some_and(|s| s.success()), &tail);
        });
        Ok(())
    }

    fn setup_line(&self, line: &str) {
        let Some(step) = setup_step(line) else { return };
        let mut inner = self.lock();
        if step > inner.setup.step {
            inner.setup.step = step;
            (self.emit)(Event::Setup(inner.setup.clone()));
        }
    }

    fn finish(&self, info: JobInfo, mut ok: bool, tail: &[String]) {
        let mut tail = tail.to_vec();
        // Without the stamp the next start would offer the same update.
        if ok
            && info.kind == JobKind::Install
            && let Some(installer) = &self.installer
            && let Err(e) = installer.record()
        {
            ok = false;
            tail.push(format!(
                "floppy: could not record the installed engine: {e}"
            ));
        }
        let install = self.installer.as_ref().and_then(Installer::need);
        let mut inner = self.lock();
        inner.install = install;
        let cancelled = inner.job.as_ref().is_some_and(|j| j.cancelled);
        inner.job = None;
        let message = engine::last_message(&tail.join("\n"))
            .unwrap_or_else(|| "the command ended with an error".into());
        if ok {
            if inner
                .last_error
                .as_ref()
                .is_some_and(|e| e.kind == info.kind)
            {
                inner.last_error = None;
            }
            if let (JobKind::Warmup, Some(name)) = (info.kind, info.name.clone()) {
                (self.emit)(Event::Notify(Notice::WarmupDone { name }));
            }
        } else if !cancelled {
            inner.last_error = Some(JobError {
                kind: info.kind,
                name: info.name.clone(),
                message: message.clone(),
            });
        }
        if matches!(info.kind, JobKind::Install | JobKind::Setup) {
            inner.setup.running = false;
            if ok {
                inner.setup.step = 4;
                inner.setup.done = true;
            } else {
                inner.setup.error = Some(SetupError {
                    kind: if message.contains("not enough free space") {
                        "space"
                    } else {
                        "other"
                    },
                    message,
                });
            }
            (self.emit)(Event::Setup(inner.setup.clone()));
        }
        // A user who started the disk is not told it "came back".
        if matches!(
            info.kind,
            JobKind::Start | JobKind::Setup | JobKind::Install
        ) {
            inner.shown = None;
        }
        self.publish(&mut inner);
        drop(inner);
        self.wake();
    }

    /// Stops a running warmup or free. Install, start, stop and setup are not
    /// interrupted: they leave the engine or the VM half done.
    pub fn cancel(&self) -> Result<(), CoreError> {
        let mut inner = self.lock();
        let job = inner.job.as_mut().ok_or(CoreError::NothingToCancel)?;
        if !matches!(job.info.kind, JobKind::Warmup | JobKind::Free) {
            return Err(CoreError::NothingToCancel);
        }
        job.cancelled = true;
        engine::signal_group(job.pid, libc::SIGTERM);
        Ok(())
    }

    /// Forgets the last job error.
    pub fn dismiss_error(&self) {
        let mut inner = self.lock();
        inner.last_error = None;
        self.publish(&mut inner);
    }
}

/// Maps a line of `floppy setup` to the step it starts.
fn setup_step(line: &str) -> Option<u8> {
    if line.starts_with("Creating the VM") {
        Some(1)
    } else if line.starts_with("Starting the VM") {
        Some(2)
    } else if line.starts_with("Installing the drive") {
        Some(3)
    } else {
        None
    }
}

/// The non-hidden folders directly under `dir`, sorted by name.
pub fn list_folders(dir: &Path, timeout: Duration) -> Result<Vec<Folder>, CoreError> {
    let (tx, rx) = mpsc::channel();
    let dir = dir.to_path_buf();
    thread::spawn(move || {
        let read = std::fs::read_dir(&dir).map(|entries| {
            let mut out: Vec<Folder> = entries
                .filter_map(Result::ok)
                .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().into_owned();
                    (!name.starts_with('.')).then(|| Folder {
                        path: e.path().display().to_string(),
                        name,
                    })
                })
                .collect();
            out.sort_by_key(|f| f.name.to_lowercase());
            out
        });
        let _ = tx.send(read);
    });
    match rx.recv_timeout(timeout) {
        Ok(Ok(folders)) => Ok(folders),
        Ok(Err(e)) => Err(CoreError::Engine(EngineError::Failed(e.to_string()))),
        Err(_) => Err(CoreError::ListTimeout),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::tests::fake;
    use crate::installer::tests::{fake_bundle, installs};
    use std::sync::Mutex as StdMutex;
    use std::time::Instant;

    const CONNECTED: &str = r#"{"version":1,"configured":true,"installed":true,"stopped":false,"vm":"running","mount_point":"/Volumes/Floppy","responding":true,"drive":"active","cache_used_mb":10,"cache_limit_mb":100,"mac_free_mb":90000,"reserve_mb":20480}"#;

    fn core_with(bin: PathBuf) -> (Arc<Core>, Arc<StdMutex<Vec<Event>>>) {
        let events = Arc::new(StdMutex::new(Vec::new()));
        let sink = Arc::clone(&events);
        let core = Core::new(bin, None, move |e| sink.lock().unwrap().push(e));
        (core, events)
    }

    fn wait_idle(core: &Core) {
        let start = Instant::now();
        while core.status().job.is_some() {
            assert!(start.elapsed() < Duration::from_secs(10), "job did not end");
            thread::sleep(Duration::from_millis(20));
        }
    }

    #[test]
    fn starting_before_the_first_answer() {
        let dir = tempfile::tempdir().unwrap();
        let (core, _) = core_with(dir.path().join("floppy"));
        assert_eq!(core.status().state, DiskState::Starting);
        core.refresh();
        assert_eq!(core.status().state, DiskState::NotInstalled);
        assert_eq!(core.status().engine_error, None);
    }

    #[test]
    fn a_warmup_reports_its_folder_and_notifies() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            &format!(
                r#"case "$1" in status) echo '{CONNECTED}' ;; warmup) sleep 0.3 ;; *) exit 2 ;; esac"#
            ),
        );
        let (core, events) = core_with(bin);
        core.refresh();
        let path = "/Volumes/Floppy/Showreel 2026".to_string();
        assert_eq!(core.disk_path(&path).unwrap(), path);
        core.run_job(JobKind::Warmup, Some(path)).unwrap();
        let s = core.status();
        assert_eq!(s.state, DiskState::Connected);
        assert_eq!(s.job.unwrap().name.as_deref(), Some("Showreel 2026"));
        assert_eq!(core.run_job(JobKind::Stop, None), Err(CoreError::Busy));
        wait_idle(&core);
        let notified = events.lock().unwrap().iter().any(
            |e| matches!(e, Event::Notify(Notice::WarmupDone { name }) if name == "Showreel 2026"),
        );
        assert!(notified);
    }

    #[test]
    fn paths_off_the_disk_are_refused() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(dir.path(), &format!("echo '{CONNECTED}'"));
        let (core, _) = core_with(bin);
        assert_eq!(
            core.disk_path("/Volumes/Floppy/x"),
            Err(CoreError::NotMounted)
        );
        core.refresh();
        assert!(matches!(
            core.disk_path("/Users/x"),
            Err(CoreError::NotOnDisk(_))
        ));
    }

    #[test]
    fn a_failed_job_keeps_its_message_and_cancel_is_silent() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            &format!(
                r#"case "$1" in
  status) echo '{CONNECTED}' ;;
  free) echo "floppy: /x is not on /Volumes/Floppy" >&2; exit 1 ;;
  warmup) sleep 20 ;;
esac"#
            ),
        );
        let (core, _) = core_with(bin);
        core.refresh();
        core.run_job(JobKind::Free, Some("/Volumes/Floppy/A".into()))
            .unwrap();
        wait_idle(&core);
        let err = core.status().last_error.unwrap();
        assert_eq!(err.kind, JobKind::Free);
        assert_eq!(err.message, "/x is not on /Volumes/Floppy");
        core.dismiss_error();
        assert_eq!(core.status().last_error, None);

        core.run_job(JobKind::Warmup, Some("/Volumes/Floppy/B".into()))
            .unwrap();
        core.cancel().unwrap();
        wait_idle(&core);
        assert_eq!(core.status().last_error, None);
    }

    #[test]
    fn setup_moves_through_its_steps() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            r#"[ "$1" = setup ] || exit 2
echo "Creating the VM (first time, a few minutes)..." >&2
echo "Starting the VM..." >&2
echo "Installing the drive in the VM (cache up to 90 GB)..." >&2
echo "Ready: /Volumes/Floppy" >&2"#,
        );
        let (core, events) = core_with(bin);
        core.run_job(JobKind::Setup, None).unwrap();
        wait_idle(&core);
        let setup = core.setup();
        assert!(setup.done && !setup.running && setup.step == 4);
        let steps: Vec<u8> = events
            .lock()
            .unwrap()
            .iter()
            .filter_map(|e| match e {
                Event::Setup(s) if s.running => Some(s.step),
                _ => None,
            })
            .collect();
        assert_eq!(steps, [1, 2, 3]);
    }

    #[test]
    fn setup_out_of_space_is_its_own_error() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            "echo 'floppy: not enough free space on this Mac: 3 GB free, at least 30 GB needed' >&2; exit 1",
        );
        let (core, _) = core_with(bin);
        core.run_job(JobKind::Setup, None).unwrap();
        wait_idle(&core);
        let err = core.setup().error.unwrap();
        assert_eq!(err.kind, "space");
    }

    #[test]
    fn reconnection_is_notified_once() {
        let dir = tempfile::tempdir().unwrap();
        let flag = dir.path().join("up");
        let bin = fake(
            dir.path(),
            &format!(
                r#"if [ -f "{}" ]; then echo '{CONNECTED}'; else echo '{}'; fi"#,
                flag.display(),
                CONNECTED.replace(
                    r#""mount_point":"/Volumes/Floppy""#,
                    r#""mount_point":null"#
                )
            ),
        );
        let (core, events) = core_with(bin);
        core.refresh();
        assert_eq!(core.status().state, DiskState::Unreachable);
        assert_eq!(core.status().next_check_s, RETRY_INTERVAL.as_secs());
        std::fs::write(&flag, "").unwrap();
        core.refresh();
        core.refresh();
        let count = events
            .lock()
            .unwrap()
            .iter()
            .filter(|e| matches!(e, Event::Notify(Notice::Reconnected)))
            .count();
        assert_eq!(count, 1);
    }

    #[test]
    fn folders_are_listed_alphabetically_without_hidden_ones() {
        let dir = tempfile::tempdir().unwrap();
        for d in ["b project", "A project", ".Trashes"] {
            std::fs::create_dir(dir.path().join(d)).unwrap();
        }
        std::fs::write(dir.path().join("loose.mov"), "").unwrap();
        let names: Vec<String> = list_folders(dir.path(), LIST_TIMEOUT)
            .unwrap()
            .into_iter()
            .map(|f| f.name)
            .collect();
        assert_eq!(names, ["A project", "b project"]);
    }

    #[test]
    fn wrong_mount_point_is_flagged() {
        let dir = tempfile::tempdir().unwrap();
        let bin = fake(
            dir.path(),
            &format!(
                "echo '{}'",
                CONNECTED.replace("/Volumes/Floppy", "/Volumes/Floppy-1")
            ),
        );
        let (core, _) = core_with(bin);
        core.refresh();
        assert!(core.status().wrong_mount_point);
    }

    fn core_installing(
        dir: &Path,
        build: &str,
        script: &str,
    ) -> (Arc<Core>, Arc<StdMutex<Vec<Event>>>, Installer) {
        let bundle = fake_bundle(dir, build, script);
        let home = dir.join("home");
        let installer = Installer {
            bundle,
            home: home.clone(),
        };
        let events = Arc::new(StdMutex::new(Vec::new()));
        let sink = Arc::clone(&events);
        let core = Core::new(home.join("bin/floppy"), Some(installer.clone()), move |e| {
            sink.lock().unwrap().push(e)
        });
        (core, events, installer)
    }

    #[test]
    fn a_fresh_install_copies_then_sets_up() {
        let dir = tempfile::tempdir().unwrap();
        let script = format!(
            r#"{}
echo "New Floppy install."
"$FLOPPY_HOME/bin/floppy" setup"#,
            installs(&format!(
                r#"case "$1" in
  status) echo '{CONNECTED}' ;;
  setup) echo "Creating the VM (first time, a few minutes)..." >&2
         echo "Starting the VM..." >&2
         echo "Installing the drive in the VM (cache up to 90 GB)..." >&2 ;;
esac"#
            ))
        );
        let (core, events, installer) = core_installing(dir.path(), "b1", &script);
        core.refresh();
        let s = core.status();
        assert_eq!(s.state, DiskState::NotInstalled);
        assert_eq!(s.install, Some(InstallNeed::Fresh));
        core.run_job(JobKind::Install, None).unwrap();
        wait_idle(&core);
        let setup = core.setup();
        assert!(setup.done && setup.error.is_none() && setup.step == 4);
        let steps: Vec<u8> = events
            .lock()
            .unwrap()
            .iter()
            .filter_map(|e| match e {
                Event::Setup(s) if s.running => Some(s.step),
                _ => None,
            })
            .collect();
        assert_eq!(steps, [0, 1, 2, 3]);
        assert_eq!(installer.need(), None);
        core.refresh();
        let s = core.status();
        assert_eq!((s.state, s.install), (DiskState::Connected, None));
    }

    #[test]
    fn a_failed_install_keeps_offering_the_update() {
        let dir = tempfile::tempdir().unwrap();
        let script = format!(
            "{}\necho 'floppy: not enough free space on this Mac: 3 GB free' >&2; exit 1",
            installs(&format!("echo '{CONNECTED}'"))
        );
        let (core, _, _) = core_installing(dir.path(), "b2", &script);
        // An engine from the zip: installed, with no stamp.
        std::fs::create_dir_all(dir.path().join("home/bin")).unwrap();
        fake(&dir.path().join("home/bin"), &format!("echo '{CONNECTED}'"));
        core.refresh();
        assert_eq!(core.status().install, Some(InstallNeed::Update));
        core.run_job(JobKind::Install, None).unwrap();
        assert_eq!(core.status().state, DiskState::Starting);
        wait_idle(&core);
        let err = core.setup().error.unwrap();
        assert_eq!(err.kind, "space");
        let s = core.status();
        assert_eq!(s.install, Some(InstallNeed::Update));
        assert_eq!(s.last_error.unwrap().kind, JobKind::Install);
    }

    #[test]
    fn install_without_a_bundle_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let (core, _) = core_with(dir.path().join("floppy"));
        assert_eq!(
            core.run_job(JobKind::Install, None),
            Err(CoreError::NoBundle)
        );
        assert_eq!(core.status().install, None);
    }
}
