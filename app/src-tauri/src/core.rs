//! The app's state: the last report from the engine, the job the app is
//! running (one at a time), and the periodic check. Events go out through a
//! callback, so this module has no Tauri dependency and is tested with a
//! fake `floppy` command.
//!
//! In the team version it also keeps the team's disks in step with the
//! control plane (`account` hands it the answer through `TeamDisks`): it
//! writes `~/.floppy/disks.json` (`team`) and runs `floppy disks apply` with
//! the secrets on stdin, after every change and again every minute while a
//! disk waits for a remount. It never applies while the person has turned
//! Floppy off (an apply would turn it back on), during an install, setup,
//! start, stop or restart, or on a Mac with the single-user volume.

use crate::account::api::DeviceConfig;
use crate::account::credentials::DiskCredentials;
use crate::account::{MountReport, TeamDisks};
use crate::engine::{
    self, Access, DiskState, EngineDisk, EngineError, EngineReport, JobKind, MetaMode, Mode,
    Pending, Problem, Replica, RunState, Space,
};
use crate::installer::{InstallNeed, Installer};
use crate::team::{self, DisksFile, Rejected};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// How often the engine is checked while the disk works.
pub const CHECK_INTERVAL: Duration = Duration::from_secs(15);
/// How often it is checked while unreachable (DECISIONS #12: every 30 s).
pub const RETRY_INTERVAL: Duration = Duration::from_secs(30);
/// Reading the disk's top folders gives up after this: a dead SMB mount can
/// hang whoever lists it.
pub const LIST_TIMEOUT: Duration = Duration::from_secs(3);
/// How often `disks apply` runs again while a disk waits for a remount, and
/// after a failed apply (docs/ARCHITECTURE.md, "Team disks": about a minute).
pub const APPLY_RETRY: Duration = Duration::from_secs(60);
/// A disk counts as having a problem once it has stayed failed this long:
/// systemd retries a failed mount every 5 s, and a new disk's metadata route
/// becomes reachable within seconds of its creation.
pub const FAILED_GRACE: Duration = Duration::from_secs(20);

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
    /// The install this Mac runs, once the engine has answered.
    pub mode: Option<Mode>,
    /// The team's disks, in the team version or when the account has some.
    pub team: Option<TeamView>,
}

/// The team's disks as the panel and Settings show them.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TeamView {
    pub disks: Vec<DiskView>,
    /// Disks the Mac may see but cannot mount (a name the engine refuses).
    pub rejected: Vec<Rejected>,
    /// `disks apply` is running.
    pub applying: bool,
    /// Why the last apply or clear failed, until one succeeds.
    pub error: Option<String>,
    /// The VM needs more memory for its disks: Floppy has to restart.
    pub restart_needed: bool,
    /// The account has disks but this Mac runs the single-user volume, which
    /// the team engine refuses to share a VM with.
    pub refused_single: bool,
    /// Disks the person uses that have stayed failed past `FAILED_GRACE`:
    /// the faceplate shows a problem while the others work.
    pub failing: u32,
}

/// One disk: the person's choice from the disks file, the rest from the engine.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DiskView {
    pub id: String,
    pub folder: String,
    /// Its folder in the Finder, while the share is mounted.
    pub path: Option<String>,
    pub access: Access,
    pub enabled: bool,
    /// None before the engine has reported the disk.
    pub state: Option<RunState>,
    pub mounted_access: Option<Access>,
    pub pending: Option<Pending>,
    pub cache_used_mb: Option<u64>,
    pub cache_size_mb: Option<u64>,
    pub replica: Option<Replica>,
    pub meta_mode: Option<MetaMode>,
    pub online: Option<bool>,
    pub lag_ms: Option<u64>,
    /// False: no longer given to this person, removed once nothing is open.
    pub listed: bool,
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
    #[error("this Mac does not run the team version")]
    NotTeam,
    #[error("no such disk on this Mac")]
    NoSuchDisk,
    #[error(transparent)]
    Engine(#[from] EngineError),
}

struct Running {
    info: JobInfo,
    pid: u32,
    cancelled: bool,
}

/// The account's answer with what mounts it: in memory only.
struct Plan {
    config: DeviceConfig,
    credentials: DiskCredentials,
}

#[derive(Default)]
struct TeamSync {
    plan: Option<Plan>,
    /// The disks file as last read or written.
    file: Option<DisksFile>,
    /// The plan or a choice changed since the last apply.
    dirty: bool,
    applying: bool,
    clearing: bool,
    last_apply: Option<Instant>,
    last_failed: bool,
    error: Option<String>,
    rejected: Vec<Rejected>,
    /// Bumped by each clear: an apply that started before it is stale.
    generation: u64,
    /// `APPLY_RETRY`, shorter in tests.
    retry: Duration,
    /// Since when each disk has been reported failed, until it mounts.
    failed_since: std::collections::HashMap<String, Instant>,
    /// `FAILED_GRACE`, shorter in tests.
    grace: Duration,
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
    team: TeamSync,
}

type Emit = Box<dyn Fn(Event) + Send + Sync>;

/// The shared state, owned by an `Arc` and used from Tauri commands, the
/// checking thread and the job threads.
pub struct Core {
    engine: PathBuf,
    /// The engine's home, where the disks file goes.
    home: PathBuf,
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
    /// A core that runs `engine` with its home `home`, installs it from
    /// `installer` when the app carries one, and reports through `emit`.
    pub fn new(
        engine: PathBuf,
        home: PathBuf,
        installer: Option<Installer>,
        emit: impl Fn(Event) + Send + Sync + 'static,
    ) -> Arc<Self> {
        let (tx, rx) = mpsc::channel();
        let install = installer.as_ref().and_then(Installer::need);
        let file = team::read(&home).ok().flatten();
        Arc::new(Core {
            engine,
            home,
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
                team: TeamSync {
                    file,
                    retry: APPLY_RETRY,
                    grace: FAILED_GRACE,
                    ..TeamSync::default()
                },
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
        let (mut state, mut problem) = engine::derive(report, job_kind);
        // Before the first answer the app is starting, not broken.
        if inner.report.is_none() && job_kind.is_none() {
            state = DiskState::Starting;
        }
        // The first apply brings the disks and the share: starting, not empty.
        if inner.team.applying
            && (state == DiskState::NoDisks || problem == Some(Problem::NotMounted))
        {
            state = DiskState::Starting;
            problem = None;
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
            mode: ok.map(|r| r.mode),
            team: Self::team_view(inner, ok),
        }
    }

    fn team_view(inner: &Inner, report: Option<&EngineReport>) -> Option<TeamView> {
        let t = &inner.team;
        let mode = report.map(|r| r.mode);
        if mode != Some(Mode::Team) && t.plan.is_none() {
            return None;
        }
        let reported: &[EngineDisk] = report.map(|r| r.team_disks()).unwrap_or_default();
        let mount_point = report.and_then(|r| r.mount_point.clone());
        let view = |id: &str, folder: &str, access: Access, enabled: bool| {
            let e = reported.iter().find(|d| d.id == id);
            DiskView {
                id: id.to_string(),
                folder: folder.to_string(),
                path: mount_point
                    .as_ref()
                    .filter(|_| e.is_some_and(|d| d.state == RunState::Mounted))
                    .map(|mp| format!("{mp}/{folder}")),
                access,
                enabled,
                state: e.map(|d| d.state),
                mounted_access: e.and_then(|d| d.mounted_access),
                pending: e.and_then(|d| d.pending),
                cache_used_mb: e.and_then(|d| d.cache_used_mb),
                cache_size_mb: e.and_then(|d| d.cache_size_mb),
                replica: e.map(|d| d.replica),
                meta_mode: e.and_then(|d| d.mode),
                online: e.and_then(|d| d.online),
                lag_ms: e.and_then(|d| d.lag_ms),
                listed: e.is_none_or(|d| d.listed),
            }
        };
        let mut disks: Vec<DiskView> = match (&t.file, &t.plan) {
            (Some(f), _) => f
                .disks
                .iter()
                .map(|d| view(&d.id, &d.folder, d.access, d.enabled))
                .collect(),
            // Not written yet (the single-user volume, or before the first sync).
            (None, Some(p)) => p
                .config
                .disks
                .iter()
                .map(|d| view(&d.id, &d.name, d.access, true))
                .collect(),
            (None, None) => Vec::new(),
        };
        for e in reported {
            if !disks.iter().any(|d| d.id == e.id) {
                disks.push(view(&e.id, &e.folder, e.access, e.enabled));
            }
        }
        Some(TeamView {
            disks,
            rejected: t.rejected.clone(),
            applying: t.applying || t.clearing,
            error: t.error.clone(),
            restart_needed: report.is_some_and(EngineReport::restart_needed),
            refused_single: mode == Some(Mode::Single) && t.plan.is_some(),
            failing: reported
                .iter()
                .filter(|d| d.enabled && d.listed && d.state != RunState::Mounted)
                .filter(|d| {
                    t.failed_since
                        .get(&d.id)
                        .is_some_and(|since| since.elapsed() >= t.grace)
                })
                .count() as u32,
        })
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
        if !inner.team.applying && !inner.team.clearing {
            inner.team.file = team::read(&self.home).ok().flatten();
        }
        // A failure lasts until the disk mounts: a unit retrying is reported
        // failed and starting by turns.
        let disks: Vec<EngineDisk> = inner
            .report
            .as_ref()
            .and_then(|r| r.as_ref().ok())
            .map(|r| r.team_disks().to_vec())
            .unwrap_or_default();
        let failed = &mut inner.team.failed_since;
        failed.retain(|id, _| {
            disks
                .iter()
                .any(|d| &d.id == id && d.enabled && d.state != RunState::Mounted)
        });
        for d in disks
            .iter()
            .filter(|d| d.enabled && d.state == RunState::Failed)
        {
            failed.entry(d.id.clone()).or_insert_with(Instant::now);
        }
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
                core.sync();
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

    /// The folders at the top of the disk, or of `path` on it (a team
    /// disk's projects), alphabetically. Read on a thread that is abandoned
    /// after `LIST_TIMEOUT`: a dead mount blocks readdir.
    pub fn folders(&self, path: Option<&str>) -> Result<Vec<Folder>, CoreError> {
        let dir = match path {
            Some(p) => self.disk_path(p)?,
            None => self.mount_point().ok_or(CoreError::NotMounted)?,
        };
        list_folders(Path::new(&dir), LIST_TIMEOUT)
    }

    /// Starts a job: the bundled install, `floppy start|stop|setup`, or
    /// `warmup|free <path>`.
    pub fn run_job(self: &Arc<Self>, kind: JobKind, path: Option<String>) -> Result<(), CoreError> {
        let mut args: Vec<&str> = vec![match kind {
            JobKind::Install => "install",
            JobKind::Setup => "setup",
            JobKind::Start => "start",
            // The restart is a stop, then a start (below).
            JobKind::Stop | JobKind::Restart => "stop",
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
            let (status, mut tail) = engine::follow(child, |line| {
                if matches!(kind, JobKind::Install | JobKind::Setup) {
                    core.setup_line(line);
                }
            });
            let mut ok = status.is_some_and(|s| s.success());
            if ok && kind == JobKind::Restart {
                match core.restart_start() {
                    Ok(child) => {
                        let (status, more) = engine::follow(child, |_| {});
                        ok = status.is_some_and(|s| s.success());
                        tail = more;
                    }
                    Err(e) => {
                        ok = false;
                        tail = vec![format!("floppy: {e}")];
                    }
                }
            }
            core.finish(info, ok, &tail);
        });
        Ok(())
    }

    /// The second half of a restart: `floppy start`, tracked as the job's process.
    fn restart_start(&self) -> Result<Child, EngineError> {
        let child = engine::spawn(&self.engine, &["start"])?;
        if let Some(job) = self.lock().job.as_mut() {
            job.pid = child.id();
        }
        Ok(child)
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
            JobKind::Start | JobKind::Setup | JobKind::Install | JobKind::Restart
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

    // The team disks.

    fn mode(inner: &Inner) -> Option<Mode> {
        inner
            .report
            .as_ref()
            .and_then(|r| r.as_ref().ok())
            .map(|r| r.mode)
    }

    /// Turns a team disk on or off on this Mac (`enabled` in the disks file);
    /// the next apply mounts it or removes it once nothing on it is open.
    pub fn set_disk_enabled(&self, id: &str, on: bool) -> Result<(), CoreError> {
        let mut inner = self.lock();
        if Self::mode(&inner) != Some(Mode::Team) {
            return Err(CoreError::NotTeam);
        }
        let mut file = team::read(&self.home)
            .map_err(|e| CoreError::Engine(EngineError::Failed(e)))?
            .ok_or(CoreError::NoSuchDisk)?;
        if !team::set_enabled(&mut file, id, on) {
            return Err(CoreError::NoSuchDisk);
        }
        team::write(&self.home, &file)
            .map_err(|e| CoreError::Engine(EngineError::Failed(e.to_string())))?;
        inner.team.file = Some(file);
        inner.team.dirty = true;
        // A choice is applied at once, not after the backoff of a failure.
        inner.team.last_failed = false;
        self.publish(&mut inner);
        drop(inner);
        self.wake();
        Ok(())
    }

    /// Runs `disks apply` when one is due: after a change, and every minute
    /// while a disk waits for a remount or after a failure.
    fn sync(self: &Arc<Self>) {
        let mut inner = self.lock();
        let Some(Ok(report)) = inner.report.as_ref() else {
            return;
        };
        let blocked = report.mode != Mode::Team
            || report.stopped
            || inner.team.applying
            || inner.team.clearing
            || inner.job.as_ref().is_some_and(|j| {
                matches!(
                    j.info.kind,
                    JobKind::Install
                        | JobKind::Setup
                        | JobKind::Start
                        | JobKind::Stop
                        | JobKind::Restart
                )
            });
        if blocked {
            return;
        }
        let waiting = report.team_disks().iter().any(|d| d.pending.is_some());
        let t = &inner.team;
        let since_last = t.last_apply.map(|at| at.elapsed());
        let backoff_over = since_last.is_none_or(|e| e >= t.retry);
        let due = (t.dirty && (!t.last_failed || backoff_over))
            || ((waiting || t.last_failed) && backoff_over && t.file.is_some());
        if !due {
            return;
        }
        // Without a plan the file stands as it is and the VM keeps its secrets.
        let mut secrets = None;
        if let Some(plan) = &inner.team.plan {
            let previous = team::read(&self.home).ok().flatten();
            let (file, rejected) = team::build(
                &plan.config,
                &plan.credentials.valkey_username,
                previous.as_ref(),
            );
            let mounted: Vec<_> = plan
                .config
                .disks
                .iter()
                .filter(|d| file.disks.iter().any(|f| f.id == d.id))
                .cloned()
                .collect();
            secrets = Some(plan.credentials.secrets_json(&mounted));
            if let Err(e) = team::write(&self.home, &file) {
                inner.team.error = Some(format!("could not write the disks file: {e}"));
                inner.team.last_apply = Some(Instant::now());
                inner.team.last_failed = true;
                self.publish(&mut inner);
                return;
            }
            inner.team.file = Some(file);
            inner.team.rejected = rejected;
        } else if inner.team.file.is_none() {
            return;
        }
        inner.team.applying = true;
        inner.team.dirty = false;
        inner.team.last_apply = Some(Instant::now());
        let generation = inner.team.generation;
        self.publish(&mut inner);
        drop(inner);

        let core = Arc::clone(self);
        thread::spawn(move || {
            let result = engine::apply(&core.engine, secrets.as_deref());
            {
                let mut inner = core.lock();
                inner.team.applying = false;
                if inner.team.generation == generation {
                    match result {
                        Ok(_) => {
                            inner.team.error = None;
                            inner.team.last_failed = false;
                        }
                        Err(e) => {
                            inner.team.error = Some(e.to_string());
                            inner.team.last_failed = true;
                        }
                    }
                }
                core.publish(&mut inner);
            }
            core.wake();
        });
    }
}

impl TeamDisks for Core {
    fn configure(&self, config: &DeviceConfig, credentials: &DiskCredentials) {
        let mut inner = self.lock();
        let same = inner.team.plan.as_ref().is_some_and(|p| {
            p.config.disks == config.disks
                && p.config.meta_ca == config.meta_ca
                && p.config.valkey_username == config.valkey_username
                && &p.credentials == credentials
        });
        inner.team.plan = Some(Plan {
            config: config.clone(),
            credentials: credentials.clone(),
        });
        // The same disks again (a launch, a refresh): nothing to apply, but
        // the first plan of a launch sends the secrets once.
        if !same {
            inner.team.dirty = true;
            inner.team.last_failed = false;
        }
        self.publish(&mut inner);
        drop(inner);
        self.wake();
    }

    fn report(&self) -> MountReport {
        let inner = self.lock();
        let disks = inner
            .report
            .as_ref()
            .and_then(|r| r.as_ref().ok())
            .map(|r| r.team_disks().to_vec())
            .unwrap_or_default();
        mount_report(&disks)
    }

    fn clear(&self) -> Result<(), String> {
        let mut inner = self.lock();
        inner.team.plan = None;
        inner.team.dirty = false;
        inner.team.rejected.clear();
        inner.team.generation += 1;
        let mode = Self::mode(&inner);
        if mode == Some(Mode::Single) {
            self.publish(&mut inner);
            return Ok(());
        }
        inner.team.clearing = true;
        self.publish(&mut inner);
        drop(inner);
        // Without an answer yet, ask: a single-user Mac has nothing to clear.
        let single =
            mode.is_none() && engine::status(&self.engine).is_ok_and(|r| r.mode == Mode::Single);
        let result = if single {
            Ok(())
        } else {
            match engine::clear(&self.engine) {
                Ok(()) | Err(EngineError::Missing(_)) => Ok(()),
                Err(e) => Err(e.to_string()),
            }
        };
        let mut inner = self.lock();
        inner.team.clearing = false;
        inner.team.file = team::read(&self.home).ok().flatten();
        inner.team.error = result.as_ref().err().cloned();
        inner.team.last_failed = false;
        self.publish(&mut inner);
        drop(inner);
        self.wake();
        result
    }
}

/// What the heartbeat says about the mounts: the mounted disks, and the
/// worst metadata mode among them (offline, then master, then replica).
pub fn mount_report(disks: &[EngineDisk]) -> MountReport {
    let mounted: Vec<&EngineDisk> = disks
        .iter()
        .filter(|d| d.state == RunState::Mounted)
        .collect();
    let has = |m: MetaMode| mounted.iter().any(|d| d.mode == Some(m));
    let replica_mode = if has(MetaMode::Offline) {
        Some("offline")
    } else if has(MetaMode::Master) {
        Some("master")
    } else if has(MetaMode::Replica) {
        Some("replica")
    } else {
        None
    };
    MountReport {
        mounted_disk_ids: mounted.iter().map(|d| d.id.clone()).collect(),
        replica_mode: replica_mode.map(str::to_string),
        replica_lag_ms: mounted.iter().filter_map(|d| d.lag_ms).max(),
    }
}

/// Maps a line of `floppy setup` to the step it starts.
fn setup_step(line: &str) -> Option<u8> {
    if line.starts_with("Creating the VM") {
        Some(1)
    } else if line.starts_with("Starting the VM") {
        Some(2)
    } else if line.starts_with("Installing the drive")
        || line.starts_with("Installing Floppy in the VM")
    {
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
        let home = bin.with_file_name("home");
        let core = Core::new(bin, home, None, move |e| sink.lock().unwrap().push(e));
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
        let core = Core::new(
            home.join("bin/floppy"),
            home.clone(),
            Some(installer.clone()),
            move |e| sink.lock().unwrap().push(e),
        );
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

    // The team disks, against a fake engine that answers `status --json` from
    // a file, records what `disks apply` got on stdin, and clears the file.

    use crate::account::api::{Access as ApiAccess, DiskConfig, OrganizationRef, UserRef};
    use crate::account::credentials::S3Key;
    use crate::engine::tests::{DISK_A, disk_with, team_status};

    struct TeamRig {
        core: Arc<Core>,
        dir: tempfile::TempDir,
        home: PathBuf,
    }

    impl TeamRig {
        fn status(&self, json: &str) {
            std::fs::write(self.dir.path().join("status.json"), json).unwrap();
        }
        fn applies(&self) -> Vec<String> {
            let mut out = Vec::new();
            for n in 1.. {
                match std::fs::read_to_string(self.dir.path().join(format!("stdin.{n}"))) {
                    Ok(s) => out.push(s),
                    Err(_) => break,
                }
            }
            out
        }
        fn calls(&self) -> String {
            std::fs::read_to_string(self.dir.path().join("calls")).unwrap_or_default()
        }
        fn sync_and_wait(&self) {
            self.core.refresh();
            self.core.sync();
            let start = Instant::now();
            while self.core.lock().team.applying {
                assert!(
                    start.elapsed() < Duration::from_secs(10),
                    "apply did not end"
                );
                thread::sleep(Duration::from_millis(20));
            }
        }
        fn file(&self) -> serde_json::Value {
            serde_json::from_slice(&std::fs::read(self.home.join("disks.json")).unwrap()).unwrap()
        }
    }

    fn team_rig() -> TeamRig {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path().display().to_string();
        let home = dir.path().join("home");
        std::fs::create_dir_all(&home).unwrap();
        let apply_answer = format!(
            r#"{{"version":1,"mount_point":"/Volumes/Floppy","cache_per_disk_mb":10240,"vm_memory_mib":1024,"vm_memory_wanted_mib":1024,"restart_needed":false,"vm":{{"smbd":"active","drive":"active","disks":[{DISK_A}]}}}}"#
        );
        let bin = fake(
            dir.path(),
            &format!(
                r#"D="{d}"
echo "$*" >> "$D/calls"
case "$1 ${{2:-}}" in
  "status --json") cat "$D/status.json" ;;
  "disks apply")
    n=1; while [ -f "$D/stdin.$n" ]; do n=$((n+1)); done
    cat > "$D/stdin.$n"
    if [ -f "$D/apply.fail" ]; then echo "floppy: $(cat "$D/apply.fail")" >&2; exit 1; fi
    echo '{apply_answer}' ;;
  "disks clear") rm -f "$D/home/disks.json"; echo '{{"version":1,"cleared":true}}' ;;
  "stop "*|"start "*) ;;
  *) exit 2 ;;
esac"#
            ),
        );
        let core = Core::new(bin, home.clone(), None, |_| {});
        core.lock().team.retry = Duration::from_millis(600);
        let rig = TeamRig { core, dir, home };
        rig.status(&team_status(&[], "").replace(r#""configured":true"#, r#""configured":false"#));
        rig
    }

    fn plan(disks: &[(&str, &str, ApiAccess)], managed: bool) -> (DeviceConfig, DiskCredentials) {
        let config = DeviceConfig {
            valkey_username: None,
            storage_mode: if managed { "managed" } else { "manual" }.into(),
            s3_access_key_tail: None,
            meta_ca: None,
            organization: OrganizationRef {
                id: "o1".into(),
                name: "Studio Esempio".into(),
            },
            user: UserRef {
                email: "giulia@studio.test".into(),
                name: "Giulia Ferri".into(),
            },
            disks: disks
                .iter()
                .map(|(id, name, access)| DiskConfig {
                    id: id.to_string(),
                    name: name.to_string(),
                    volume_name: id.to_string(),
                    access: *access,
                    meta_url: "redis://localhost:4551/1".into(),
                    cache_expire_days: 30,
                    mount_path: format!("/Volumes/Floppy/{name}"),
                })
                .collect(),
        };
        let creds = DiskCredentials {
            valkey_username: "floppy_d_1".into(),
            valkey_password: "pw-secret".into(),
            s3: managed.then(|| S3Key {
                access_key_id: "GK123".into(),
                secret_key: "sk-secret".into(),
            }),
        };
        (config, creds)
    }

    #[test]
    fn a_plan_writes_the_disks_file_and_applies_with_the_secrets_on_stdin() {
        let r = team_rig();
        r.core.refresh();
        assert_eq!(r.core.status().state, DiskState::NoDisks);
        let (config, creds) = plan(&[("0192-a", "Archivio 2025", ApiAccess::Read)], true);
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        let file = r.file();
        assert_eq!(file["meta_user"], "floppy_d_1");
        assert_eq!(file["disks"][0]["folder"], "Archivio 2025");
        assert_eq!(file["disks"][0]["enabled"], true);
        // The file holds no secret; stdin held them all.
        let raw = std::fs::read_to_string(r.home.join("disks.json")).unwrap();
        assert!(!raw.contains("pw-secret") && !raw.contains("sk-secret"));
        let sent: serde_json::Value = serde_json::from_str(&r.applies()[0]).unwrap();
        assert_eq!(sent["meta_password"], "pw-secret");
        assert_eq!(sent["s3"]["0192-a"]["secret_key"], "sk-secret");
        assert!(!r.calls().contains("pw-secret"));

        // The same plan again (a refresh) applies nothing.
        r.status(&team_status(&[DISK_A], ""));
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 1);
        let s = r.core.status();
        assert_eq!(s.state, DiskState::Connected);
        let team = s.team.unwrap();
        assert_eq!(
            team.disks[0].path.as_deref(),
            Some("/Volumes/Floppy/Archivio 2025")
        );
        assert_eq!(team.disks[0].state, Some(RunState::Mounted));
        assert_eq!(team.error, None);
    }

    #[test]
    fn a_pending_disk_is_applied_again_until_it_is_done() {
        let r = team_rig();
        let (config, creds) = plan(&[("0192-a", "Archivio 2025", ApiAccess::Read)], false);
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        // Lowered to read while a file is open: the engine says remount.
        let waiting = disk_with(
            DISK_A,
            &[("pending", "\"remount\""), ("mounted_access", "\"write\"")],
        );
        r.status(&team_status(&[&waiting], ""));
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 1, "not before the retry interval");
        thread::sleep(Duration::from_millis(650));
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 2);
        let disk = &r.core.status().team.unwrap().disks[0];
        assert_eq!(disk.pending, Some(Pending::Remount));
        r.status(&team_status(&[DISK_A], ""));
        thread::sleep(Duration::from_millis(650));
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 2, "nothing waits any more");
    }

    #[test]
    fn a_failed_apply_is_shown_and_retried_after_the_interval() {
        let r = team_rig();
        std::fs::write(
            r.dir.path().join("apply.fail"),
            "the VM is not responding, run: floppy status",
        )
        .unwrap();
        let (config, creds) = plan(&[("0192-a", "A", ApiAccess::Write)], false);
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert_eq!(
            r.core.status().team.unwrap().error.as_deref(),
            Some("the floppy command failed: the VM is not responding, run: floppy status")
        );
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 1);
        std::fs::remove_file(r.dir.path().join("apply.fail")).unwrap();
        thread::sleep(Duration::from_millis(650));
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 2);
        assert_eq!(r.core.status().team.unwrap().error, None);
    }

    #[test]
    fn never_applies_while_off_or_on_the_single_user_volume() {
        let r = team_rig();
        let (config, creds) = plan(&[("0192-a", "A", ApiAccess::Write)], false);
        // Turned off by the person: an apply would turn Floppy back on.
        r.status(&team_status(&[], "").replace(r#""stopped":false"#, r#""stopped":true"#));
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert!(r.applies().is_empty());
        assert!(!r.home.join("disks.json").exists());

        // The zip's volume: the plan is shown as refused, nothing written.
        r.status(r#"{"version":1,"mode":"single","configured":true,"installed":true,"stopped":false,"vm":"running","mount_point":"/Volumes/Floppy","responding":true,"drive":"active","cache_used_mb":1,"cache_limit_mb":100,"mac_free_mb":90000,"reserve_mb":20480,"vm_memory_mib":null,"vm_memory_wanted_mib":null,"disks":null}"#);
        r.sync_and_wait();
        assert!(r.applies().is_empty());
        let s = r.core.status();
        assert_eq!(s.state, DiskState::Connected);
        let team = s.team.unwrap();
        assert!(team.refused_single);
        assert_eq!(team.disks[0].folder, "A");
        assert_eq!(
            r.core.set_disk_enabled("0192-a", false),
            Err(CoreError::NotTeam)
        );
        // Nothing to clear there: the volume is not touched.
        assert_eq!(r.core.clear(), Ok(()));
        assert!(!r.calls().contains("disks clear"));

        // On again: the waiting plan is applied.
        r.status(&team_status(&[], ""));
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 1);
    }

    #[test]
    fn a_disk_turned_off_stays_off_across_new_answers() {
        let r = team_rig();
        let (config, creds) = plan(
            &[
                ("0192-a", "A", ApiAccess::Write),
                ("0192-b", "B", ApiAccess::Read),
            ],
            false,
        );
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        r.core.set_disk_enabled("0192-b", false).unwrap();
        assert_eq!(r.file()["disks"][1]["enabled"], false);
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 2, "a choice is applied at once");
        assert_eq!(
            r.core.set_disk_enabled("nope", true),
            Err(CoreError::NoSuchDisk)
        );
        // The server lowers A: B stays off.
        let (lowered, _) = plan(
            &[
                ("0192-a", "A", ApiAccess::Read),
                ("0192-b", "B", ApiAccess::Read),
            ],
            false,
        );
        r.core.configure(&lowered, &creds);
        r.sync_and_wait();
        let file = r.file();
        assert_eq!(file["disks"][0]["access"], "read");
        assert_eq!(file["disks"][1]["enabled"], false);
    }

    #[test]
    fn clear_runs_the_engine_and_forgets_the_plan() {
        let r = team_rig();
        let (config, creds) = plan(&[("0192-a", "A", ApiAccess::Write)], false);
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert!(r.home.join("disks.json").exists());
        assert_eq!(r.core.clear(), Ok(()));
        assert!(r.calls().contains("disks clear"));
        assert!(!r.home.join("disks.json").exists());
        r.status(&team_status(&[], "").replace(r#""configured":true"#, r#""configured":false"#));
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 1, "no plan, no file: nothing to apply");
        assert!(r.core.status().team.unwrap().disks.is_empty());
    }

    #[test]
    fn a_failure_counts_only_past_the_grace_and_until_it_mounts() {
        let r = team_rig();
        r.core.lock().team.grace = Duration::from_millis(300);
        let failed = disk_with(
            DISK_A,
            &[
                ("state", "\"failed\""),
                ("mode", "null"),
                ("online", "null"),
            ],
        );
        let other = disk_with(DISK_A, &[("id", "\"b\""), ("folder", "\"B\"")]);
        r.status(&team_status(&[&failed, &other], ""));
        r.core.refresh();
        // A new disk retrying while its route comes up: not a problem yet.
        assert_eq!(r.core.status().team.unwrap().failing, 0);
        thread::sleep(Duration::from_millis(350));
        // Retrying: starting, then failed again; the clock keeps running.
        let retrying = disk_with(DISK_A, &[("state", "\"starting\"")]);
        r.status(&team_status(&[&retrying, &other], ""));
        r.core.refresh();
        let s = r.core.status();
        assert_eq!(s.team.unwrap().failing, 1);
        assert_eq!(s.state, DiskState::Connected, "the others work");
        r.status(&team_status(&[DISK_A, &other], ""));
        r.core.refresh();
        assert_eq!(r.core.status().team.unwrap().failing, 0);
    }

    #[test]
    fn an_unlisted_disk_is_shown_until_it_goes() {
        let r = team_rig();
        let (config, creds) = plan(&[("0192-a", "Archivio 2025", ApiAccess::Write)], false);
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        let gone = disk_with(
            DISK_A,
            &[
                ("id", "\"0192-z\""),
                ("folder", "\"Vecchio\""),
                ("listed", "false"),
                ("enabled", "false"),
                ("pending", "\"remove\""),
            ],
        );
        r.status(&team_status(&[DISK_A, &gone], ""));
        r.core.refresh();
        let team = r.core.status().team.unwrap();
        let disk = team.disks.iter().find(|d| d.id == "0192-z").unwrap();
        assert!(!disk.listed && !disk.enabled);
        assert_eq!(disk.pending, Some(Pending::Remove));
        assert!(team.disks.iter().find(|d| d.id == "0192-a").unwrap().listed);
    }

    #[test]
    fn the_metadata_ca_reaches_the_file_and_a_new_one_applies() {
        let r = team_rig();
        let (mut config, creds) = plan(&[("0192-a", "A", ApiAccess::Write)], false);
        r.core.refresh();
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert_eq!(r.file()["meta_ca"], serde_json::Value::Null);
        config.meta_ca = Some("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n".into());
        r.core.configure(&config, &creds);
        r.sync_and_wait();
        assert_eq!(r.applies().len(), 2);
        assert!(
            r.file()["meta_ca"]
                .as_str()
                .unwrap()
                .starts_with("-----BEGIN")
        );
    }

    #[test]
    fn restart_stops_then_starts() {
        let r = team_rig();
        r.status(
            &team_status(&[DISK_A], "")
                .replace(r#""vm_memory_mib":1024"#, r#""vm_memory_mib":768"#),
        );
        r.core.refresh();
        assert!(r.core.status().team.unwrap().restart_needed);
        r.core.run_job(JobKind::Restart, None).unwrap();
        assert_eq!(r.core.status().state, DiskState::Starting);
        wait_idle(&r.core);
        assert!(r.calls().ends_with("stop\nstart\n"), "{}", r.calls());
        assert_eq!(r.core.status().last_error, None);
    }

    #[test]
    fn the_heartbeat_report_takes_the_worst_mode() {
        let offline = disk_with(
            DISK_A,
            &[
                ("id", "\"b\""),
                ("mode", "\"offline\""),
                ("online", "false"),
            ],
        );
        let starting = disk_with(
            DISK_A,
            &[("id", "\"c\""), ("state", "\"starting\""), ("mode", "null")],
        );
        let disks: Vec<EngineDisk> = [DISK_A, offline.as_str(), starting.as_str()]
            .iter()
            .map(|d| serde_json::from_str(d).unwrap())
            .collect();
        let report = mount_report(&disks);
        assert_eq!(report.mounted_disk_ids, ["0192-a", "b"]);
        assert_eq!(report.replica_mode.as_deref(), Some("offline"));
        assert_eq!(report.replica_lag_ms, None);
        let lagging = disk_with(DISK_A, &[("lag_ms", "1500")]);
        let quick = disk_with(DISK_A, &[("id", "\"q\""), ("lag_ms", "500")]);
        let with_lag: Vec<EngineDisk> = [lagging, quick]
            .iter()
            .map(|d| serde_json::from_str(d).unwrap())
            .collect();
        assert_eq!(mount_report(&with_lag).replica_lag_ms, Some(1500));
        assert_eq!(
            mount_report(&disks[..1]).replica_mode.as_deref(),
            Some("replica")
        );
        assert_eq!(mount_report(&[]).replica_mode, None);
    }
}
