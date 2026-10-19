//! The disks' activity in the app (DECISIONS #27). Every few seconds it reads
//! what this Mac did and is writing from the engine (`floppy activity`), sends
//! what concerns the team's disks to the control plane and takes back what
//! anyone did on them and what the other Macs are writing. From both it keeps
//! the Activity tab's feed, the live states of the panel ("arriving", "in
//! use") and the notices with an undo: a mass delete for whoever did it, a
//! folder renamed for everyone who sees the disk. Without the control plane
//! it still knows this Mac's own events, and the trash still works.
//!
//! Only what happens while the app runs, or reaches it after a pause, can
//! notify: what the engine had recorded before the app first read it, and the
//! control plane's events before the first report, never do.

use crate::account::api::{
    ActivityApi, ActivityRequest, ApiError, DeleteQuery, DeletesRequest, EventIn, PersonRef,
    RemoteEvent, RemoteWriting, WritingIn,
};
use crate::core::{Core, CoreError, RestoreView};
use crate::engine::{
    self, Access, ActivityReport, LocalEvent, Op, OpenFile, RenameBackRefusal, TrashKind,
    TrashReport, Writing,
};
use crate::prefs::{ActivityPrefs, PrefsStore};
use serde::Serialize;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// How often the engine and the control plane are asked.
pub const POLL: Duration = Duration::from_secs(5);
/// A mass delete (DECISIONS #27): a folder, or more than 20 files or 5 GB.
pub const MASS_FILES: u64 = 20;
pub const MASS_BYTES: u64 = 5_000_000_000;
/// The Finder deletes a folder file by file: the notice waits for a pause.
pub const SETTLE: Duration = Duration::from_secs(4);
/// A folder renamed this soon after it was made is a new folder taking its
/// name (the Finder makes "senza titolo" first), not a project renamed.
pub const NEW_FOLDER_S: f64 = 600.0;
/// Older events reaching the app (a Mac back from a long sleep) do not notify.
pub const NOTIFY_WITHIN_S: f64 = 86_400.0;
/// A trash entry and the event of its delete are this close in time.
pub const DELETE_WINDOW_S: f64 = 120.0;
/// Events kept in memory, this Mac's and the others'.
const KEEP: usize = 5000;
/// A page of this Mac's own events in the feed.
const LOCAL_PAGE: usize = 200;
/// Pages of the engine or the server read in one pass at most.
const PAGES: usize = 10;
/// Events per report: the server's limit.
const REPORT_PAGE: usize = 500;
const MAX_RENAME_NOTICES: usize = 5;

/// Where and as whom this Mac calls the control plane.
#[derive(Clone, PartialEq, Eq)]
pub struct DeviceAuth {
    pub server: String,
    pub token: String,
}

impl std::fmt::Debug for DeviceAuth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DeviceAuth")
            .field("server", &self.server)
            .finish_non_exhaustive()
    }
}

/// The account as this module needs it: `account::Account` in the app.
pub trait Session: Send + Sync {
    /// The server and token while this Mac is signed in and registered.
    fn device(&self) -> Option<DeviceAuth>;
}

/// An event as the Activity tab shows it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FeedEvent {
    /// `r<server id>` or `l<local id>`: unique in a feed.
    pub id: String,
    pub disk: Option<String>,
    /// Unix seconds.
    pub at: f64,
    pub op: Op,
    pub kind: TrashKind,
    pub path: String,
    pub to: Option<String>,
    pub size: Option<u64>,
    pub by: PersonRef,
    /// Done on this Mac.
    pub this_mac: bool,
}

/// A file being written now, on this Mac or another.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LiveFile {
    pub disk: Option<String>,
    pub path: String,
    /// `added`: arriving; `written` or `replaced`: in use.
    pub op: Op,
    pub since: f64,
    pub size: Option<u64>,
    /// Bytes a second since it was opened: a Finder copy grows in 64 MB
    /// steps, so the average says more than the last step.
    pub speed: Option<f64>,
    pub by: PersonRef,
    pub this_mac: bool,
}

/// A page of the Activity tab.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FeedPage {
    /// Newest first.
    pub events: Vec<FeedEvent>,
    /// Asks for the next page; None at the end.
    pub before: Option<String>,
    /// Colleagues' events are in: false for the single-user volume, signed
    /// out, or without the control plane.
    pub shared: bool,
    /// Since when the control plane has not answered.
    pub offline_since_ms: Option<u64>,
    /// What is being written on the disk now.
    pub live: Vec<LiveFile>,
}

/// Something deleted, as an undo puts it back.
#[derive(Debug, Clone, PartialEq, Serialize, serde::Deserialize)]
pub struct DeletedItem {
    pub path: String,
    pub at: f64,
}

/// What a notice is about.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NoticeKind {
    /// This Mac deleted a folder, or many files at once. `items` are what
    /// the trash lists: the folders and the loose files.
    MassDelete {
        disk: Option<String>,
        items: Vec<DeletedItem>,
        /// The folder's name when one folder went.
        name: Option<String>,
        files: u64,
        bytes: u64,
        at: f64,
    },
    /// A folder renamed or moved; `by.you` when this Mac did it.
    Renamed {
        disk: Option<String>,
        path: String,
        to: String,
        by: PersonRef,
        at: f64,
    },
    /// Someone gave a folder this Mac renamed its name of before.
    RenamedBack {
        disk: Option<String>,
        path: String,
        to: String,
        by: PersonRef,
    },
}

/// Where a notice's undo is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NoticeState {
    Open,
    Working,
    Done,
    Failed,
}

/// A notice in the panel, until it is closed or its undo no longer applies.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ActivityNotice {
    pub id: u64,
    #[serde(flatten)]
    pub kind: NoticeKind,
    /// The disk's folder in the Finder; None for the single-user volume.
    pub folder: Option<String>,
    /// This Mac may write the disk: the undo is offered.
    pub can_undo: bool,
    pub state: NoticeState,
    /// Why the undo failed: `taken`, `gone`, `no_folder`, `not_in_trash`,
    /// or the engine's message.
    pub error: Option<String>,
    /// Where the result is in the Finder, once done.
    pub finder_path: Option<String>,
}

/// A macOS notification, worded by the panel's dictionary. It informs; the
/// undo stays in the panel (DECISIONS #27).
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Alert {
    MassDelete {
        folder: Option<String>,
        name: Option<String>,
        files: u64,
    },
    Renamed {
        by: PersonRef,
        from: String,
        to: String,
    },
    /// `from` is the name this Mac had given, `to` the one back.
    RenamedBack {
        by: String,
        from: String,
        to: String,
    },
    /// This Mac opened a file a colleague is still copying.
    Arriving {
        by: String,
        file: String,
        folder: String,
        size: Option<u64>,
    },
    /// The colleague finished copying into that folder.
    Arrived { by: String, folder: String },
}

/// What the panel and the disk window show live.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ActivitySnapshot {
    pub notices: Vec<ActivityNotice>,
    pub live: Vec<LiveFile>,
}

/// What this module tells the app shell.
#[derive(Debug, Clone)]
pub enum ActivityEvent {
    Changed(Box<ActivitySnapshot>),
    Alert(Alert),
}

struct Burst {
    events: Vec<LocalEvent>,
    last: Instant,
}

#[derive(Default)]
struct State {
    /// This Mac's events, oldest first.
    local: VecDeque<LocalEvent>,
    /// The last id read from the engine.
    local_last: u64,
    /// Events above this id are new: they may notify. None before the
    /// first read.
    seen: Option<u64>,
    /// The last id the control plane stored.
    local_sent: u64,
    cursor: Option<String>,
    /// The server `local_sent` and `cursor` belong to.
    server: Option<String>,
    /// Everyone's events on the team's disks, from the reports, oldest first.
    remote: VecDeque<RemoteEvent>,
    writing: Vec<Writing>,
    open: Vec<OpenFile>,
    others: Vec<RemoteWriting>,
    offline_since_ms: Option<u64>,
    notices: Vec<ActivityNotice>,
    next_notice: u64,
    bursts: HashMap<Option<String>, Burst>,
    /// Folders (disk, folder path) where this Mac opened a file still
    /// arriving, with who copies it.
    told: HashMap<(String, String), String>,
}

type Emit = Box<dyn Fn(ActivityEvent) + Send + Sync>;

/// The activity service: one worker thread, shared with the commands.
pub struct Activity {
    core: Arc<Core>,
    api: Arc<dyn ActivityApi>,
    session: Arc<dyn Session>,
    prefs: PrefsStore,
    state: Mutex<State>,
    emit: Emit,
    worker: AtomicBool,
}

fn now_s() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn now_ms() -> u64 {
    (now_s() * 1000.0) as u64
}

/// Unix seconds of an ISO 8601 time in UTC as the server writes it
/// (`2026-10-19T10:00:00.25Z` or `+00:00`); None for anything else.
pub fn epoch(s: &str) -> Option<f64> {
    let s = s.strip_suffix('Z').or_else(|| s.strip_suffix("+00:00"))?;
    let (date, time) = s.split_once('T')?;
    let mut d = date.split('-').map(|p| p.parse::<i64>().ok());
    let (y, m, day) = (d.next()??, d.next()??, d.next()??);
    let (hms, frac) = time.split_once('.').unwrap_or((time, ""));
    let mut t = hms.split(':').map(|p| p.parse::<i64>().ok());
    let (h, mi, sec) = (t.next()??, t.next()??, t.next()??);
    if !(1..=12).contains(&m) || !(1..=31).contains(&day) || h > 23 || mi > 59 || sec > 60 {
        return None;
    }
    // Days from the civil date (Howard Hinnant's algorithm).
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    let fraction = if frac.is_empty() {
        0.0
    } else {
        format!("0.{frac}").parse::<f64>().ok()?
    };
    Some((days * 86_400 + h * 3600 + mi * 60 + sec) as f64 + fraction)
}

fn parent(path: &str) -> &str {
    path.rsplit_once('/').map_or("", |(p, _)| p)
}

fn base(path: &str) -> &str {
    path.rsplit_once('/').map_or(path, |(_, b)| b)
}

/// `path` is `folder` or inside it.
fn under(path: &str, folder: &str) -> bool {
    path == folder
        || path
            .strip_prefix(folder)
            .is_some_and(|rest| rest.starts_with('/'))
}

fn you() -> PersonRef {
    PersonRef {
        name: String::new(),
        you: true,
    }
}

fn local_feed(e: &LocalEvent) -> FeedEvent {
    FeedEvent {
        id: format!("l{}", e.id),
        disk: e.disk.clone(),
        at: e.at,
        op: e.op,
        kind: e.kind,
        path: e.path.clone(),
        to: e.to.clone(),
        size: e.size,
        by: you(),
        this_mac: true,
    }
}

fn remote_feed(e: &RemoteEvent) -> FeedEvent {
    FeedEvent {
        id: format!("r{}", e.id),
        disk: Some(e.disk_id.clone()),
        at: epoch(&e.at).unwrap_or(0.0),
        op: e.op,
        kind: e.kind,
        path: e.path.clone(),
        to: e.to.clone(),
        size: e.size,
        by: e.by.clone(),
        this_mac: e.this_device,
    }
}

/// Bytes a second since `since`, once the file has some.
fn speed(size: Option<u64>, since: f64, now: f64) -> Option<f64> {
    let size = size.filter(|s| *s > 0)?;
    Some(size as f64 / (now - since).max(1.0))
}

impl Activity {
    /// The service; `start_worker` runs it.
    pub fn new(
        core: Arc<Core>,
        api: Arc<dyn ActivityApi>,
        session: Arc<dyn Session>,
        prefs: PrefsStore,
        emit: impl Fn(ActivityEvent) + Send + Sync + 'static,
    ) -> Arc<Self> {
        let stored = prefs.load().activity;
        Arc::new(Activity {
            core,
            api,
            session,
            prefs,
            state: Mutex::new(State {
                seen: stored.local_seen,
                local_sent: stored.local_sent,
                cursor: stored.cursor,
                server: stored.server,
                ..State::default()
            }),
            emit: Box::new(emit),
            worker: AtomicBool::new(false),
        })
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Starts the worker thread; later calls do nothing.
    pub fn start_worker(self: &Arc<Self>) {
        if self.worker.swap(true, Ordering::SeqCst) {
            return;
        }
        let me = Arc::downgrade(self);
        thread::spawn(move || {
            while let Some(a) = me.upgrade() {
                a.tick();
                drop(a);
                thread::sleep(POLL);
            }
        });
    }

    /// The notices and what is being written now.
    pub fn snapshot(&self) -> ActivitySnapshot {
        let state = self.lock();
        ActivitySnapshot {
            notices: state.notices.clone(),
            live: Self::live(&state, None),
        }
    }

    fn publish(&self, state: &State) {
        (self.emit)(ActivityEvent::Changed(Box::new(ActivitySnapshot {
            notices: state.notices.clone(),
            live: Self::live(state, None),
        })));
    }

    fn live(state: &State, disk: Option<Option<&str>>) -> Vec<LiveFile> {
        let now = now_s();
        let mine = state.writing.iter().map(|w| LiveFile {
            disk: w.disk.clone(),
            path: w.path.clone(),
            op: w.op,
            since: w.since,
            size: w.size,
            speed: speed(w.size, w.since, now),
            by: you(),
            this_mac: true,
        });
        let others = state.others.iter().map(|w| {
            let since = epoch(&w.since).unwrap_or(now);
            LiveFile {
                disk: Some(w.disk_id.clone()),
                path: w.path.clone(),
                op: w.op,
                since,
                size: w.size,
                speed: speed(w.size, since, now),
                by: w.by.clone(),
                this_mac: false,
            }
        });
        mine.chain(others)
            .filter(|l| disk.is_none_or(|d| l.disk.as_deref() == d))
            .collect()
    }

    /// One pass: the engine, then the control plane, then the notices.
    pub fn tick(&self) {
        if !self.core.activity_ready() {
            return;
        }
        let mut reports = Vec::new();
        let mut after = self.lock().local_last;
        for _ in 0..PAGES {
            match engine::activity(self.core.engine(), after) {
                Ok(r) => {
                    let full = r.events.len() as u64 >= engine::ACTIVITY_PAGE;
                    after = r.last;
                    reports.push(r);
                    if !full {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
        if reports.is_empty() {
            return;
        }
        let mut changed = self.take_local(reports);
        if let Some(auth) = self.session.device() {
            changed |= self.exchange(&auth);
        } else {
            let mut state = self.lock();
            changed |= !state.others.is_empty();
            state.others.clear();
            state.offline_since_ms = None;
        }
        changed |= self.arrivals();
        changed |= self.settle_bursts(Instant::now());
        self.save();
        if changed {
            let state = self.lock();
            self.publish(&state);
        }
    }

    fn take_local(&self, reports: Vec<ActivityReport>) -> bool {
        let mut state = self.lock();
        let first = state.seen.is_none();
        let mut changed = false;
        for r in reports {
            changed |= state.writing != r.writing || !r.events.is_empty();
            state.writing = r.writing;
            state.open = r.open;
            // A VM made again starts its ids above the old ones (`floppy-activity`);
            // an older engine restarted at 1: what it lists is new.
            if r.last < state.local_last {
                state.seen = Some(0);
                state.local_sent = 0;
            }
            state.local_last = r.last;
            for e in r.events {
                if !first && state.seen.is_some_and(|s| e.id > s) {
                    self.on_local(&mut state, &e);
                }
                state.local.push_back(e);
            }
        }
        while state.local.len() > KEEP {
            state.local.pop_front();
        }
        state.seen = Some(state.local_last);
        changed
    }

    fn on_local(&self, state: &mut State, e: &LocalEvent) {
        if now_s() - e.at > NOTIFY_WITHIN_S {
            return;
        }
        match (e.op, e.kind) {
            (Op::Deleted, _) => {
                let burst = state.bursts.entry(e.disk.clone()).or_insert(Burst {
                    events: Vec::new(),
                    last: Instant::now(),
                });
                burst.events.push(e.clone());
                burst.last = Instant::now();
            }
            (Op::Renamed, TrashKind::Folder) => {
                let Some(to) = e.to.as_deref() else { return };
                self.supersede(state, e.disk.as_deref(), &e.path);
                // This Mac undoing a colleague's rename: that notice is done.
                if let Some(n) = state.notices.iter_mut().find(|n| {
                    matches!(&n.kind, NoticeKind::Renamed { disk, path, to: t, .. }
                        if *disk == e.disk && path == to && t == &e.path)
                }) {
                    n.state = NoticeState::Done;
                    return;
                }
                if self.recently_made(state, e.disk.as_deref(), &e.path, e.at) {
                    return;
                }
                let folder = self.core.disk_folder(e.disk.as_deref());
                self.add_notice(
                    state,
                    NoticeKind::Renamed {
                        disk: e.disk.clone(),
                        path: e.path.clone(),
                        to: to.to_string(),
                        by: you(),
                        at: e.at,
                    },
                    folder,
                );
                (self.emit)(ActivityEvent::Alert(Alert::Renamed {
                    by: you(),
                    from: base(&e.path).to_string(),
                    to: base(to).to_string(),
                }));
            }
            _ => {}
        }
    }

    /// A folder made at `path` shortly before `at`, by anyone.
    fn recently_made(&self, state: &State, disk: Option<&str>, path: &str, at: f64) -> bool {
        let made = |d: Option<&str>, op: Op, p: &str, t: f64| {
            d == disk && op == Op::Created && p == path && at - t >= 0.0 && at - t < NEW_FOLDER_S
        };
        state
            .local
            .iter()
            .any(|e| made(e.disk.as_deref(), e.op, &e.path, e.at))
            || state.remote.iter().any(|e| {
                made(
                    Some(&e.disk_id),
                    e.op,
                    &e.path,
                    epoch(&e.at).unwrap_or(f64::MIN),
                )
            })
    }

    /// A rename notice whose folder changed again no longer applies.
    fn supersede(&self, state: &mut State, disk: Option<&str>, path: &str) {
        state.notices.retain(|n| match &n.kind {
            NoticeKind::Renamed { disk: d, to, .. } => {
                n.state == NoticeState::Done || d.as_deref() != disk || !under(to, path)
            }
            _ => true,
        });
    }

    fn add_notice(&self, state: &mut State, kind: NoticeKind, folder: Option<String>) {
        let disk = match &kind {
            NoticeKind::MassDelete { disk, .. }
            | NoticeKind::Renamed { disk, .. }
            | NoticeKind::RenamedBack { disk, .. } => disk.clone(),
        };
        let can_undo = !matches!(kind, NoticeKind::RenamedBack { .. })
            && self.core.disk_access(disk.as_deref()) == Some(Access::Write);
        state.next_notice += 1;
        state.notices.push(ActivityNotice {
            id: state.next_notice,
            kind,
            folder,
            can_undo,
            state: NoticeState::Open,
            error: None,
            finder_path: None,
        });
        let renames: Vec<u64> = state
            .notices
            .iter()
            .filter(|n| matches!(n.kind, NoticeKind::Renamed { .. }))
            .map(|n| n.id)
            .collect();
        if renames.len() > MAX_RENAME_NOTICES {
            let drop: HashSet<u64> = renames[..renames.len() - MAX_RENAME_NOTICES]
                .iter()
                .copied()
                .collect();
            state.notices.retain(|n| !drop.contains(&n.id));
        }
    }

    /// Deletes that paused long enough become a notice when they are many.
    fn settle_bursts(&self, now: Instant) -> bool {
        let mut state = self.lock();
        let done: Vec<Option<String>> = state
            .bursts
            .iter()
            .filter(|(_, b)| now.duration_since(b.last) >= SETTLE)
            .map(|(d, _)| d.clone())
            .collect();
        let mut changed = false;
        for disk in done {
            let Some(burst) = state.bursts.remove(&disk) else {
                continue;
            };
            let folders: Vec<&LocalEvent> = burst
                .events
                .iter()
                .filter(|e| e.kind == TrashKind::Folder)
                .collect();
            let top_folders: Vec<&LocalEvent> = folders
                .iter()
                .copied()
                .filter(|f| {
                    !folders
                        .iter()
                        .any(|g| g.path != f.path && under(&f.path, &g.path))
                })
                .collect();
            let files: Vec<&LocalEvent> = burst
                .events
                .iter()
                .filter(|e| e.kind == TrashKind::File)
                .collect();
            let bytes: u64 = files.iter().filter_map(|e| e.size).sum();
            if top_folders.is_empty() && files.len() as u64 <= MASS_FILES && bytes <= MASS_BYTES {
                continue;
            }
            let mut items: Vec<DeletedItem> = top_folders
                .iter()
                .map(|f| DeletedItem {
                    path: f.path.clone(),
                    at: f.at,
                })
                .collect();
            items.extend(
                files
                    .iter()
                    .filter(|e| !top_folders.iter().any(|f| under(&e.path, &f.path)))
                    .map(|e| DeletedItem {
                        path: e.path.clone(),
                        at: e.at,
                    }),
            );
            let name = match top_folders.as_slice() {
                [one] if items.len() == 1 => Some(base(&one.path).to_string()),
                _ => None,
            };
            let at = burst.events.iter().map(|e| e.at).fold(0.0, f64::max);
            let folder = self.core.disk_folder(disk.as_deref());
            (self.emit)(ActivityEvent::Alert(Alert::MassDelete {
                folder: folder.clone(),
                name: name.clone(),
                files: files.len() as u64,
            }));
            self.add_notice(
                &mut state,
                NoticeKind::MassDelete {
                    disk,
                    items,
                    name,
                    files: files.len() as u64,
                    bytes,
                    at,
                },
                folder,
            );
            changed = true;
        }
        changed
    }

    /// Sends this Mac's new events and writes, takes the others'.
    fn exchange(&self, auth: &DeviceAuth) -> bool {
        let mut changed = false;
        {
            let mut state = self.lock();
            if state.server.as_deref() != Some(&auth.server) {
                // Another server, or the first sign-in: what was recorded
                // before belongs to no one there.
                state.server = Some(auth.server.clone());
                state.cursor = None;
                state.local_sent = state.local_last;
                state.remote.clear();
            }
        }
        for _ in 0..PAGES {
            let (request, last_sent) = {
                let state = self.lock();
                let events: Vec<EventIn> = state
                    .local
                    .iter()
                    .filter(|e| e.id > state.local_sent)
                    .filter_map(|e| {
                        Some(EventIn {
                            local_id: e.id,
                            at: e.at,
                            disk_id: e.disk.clone()?,
                            op: e.op,
                            kind: e.kind,
                            path: e.path.clone(),
                            to: e.to.clone(),
                            size: e.size,
                        })
                    })
                    .take(REPORT_PAGE)
                    .collect();
                // Nothing left on a team disk: the single-user volume's never go.
                let last_sent = match events.last() {
                    Some(e) if events.len() == REPORT_PAGE => e.local_id,
                    _ => state.local_last,
                };
                let writing = state
                    .writing
                    .iter()
                    .filter_map(|w| {
                        Some(WritingIn {
                            disk_id: w.disk.clone()?,
                            path: w.path.clone(),
                            op: w.op,
                            since: w.since,
                            size: w.size,
                        })
                    })
                    .collect();
                (
                    ActivityRequest {
                        events,
                        writing,
                        after: state.cursor.clone(),
                    },
                    last_sent,
                )
            };
            let sent_all = last_sent == self.lock().local_last;
            match self.api.activity(&auth.server, &auth.token, &request) {
                Ok(answer) => {
                    let mut state = self.lock();
                    state.local_sent = last_sent;
                    state.offline_since_ms = None;
                    let first = request.after.is_none();
                    changed |= state.others != answer.writing || !answer.events.is_empty();
                    state.others = answer.writing;
                    for e in answer.events {
                        if !first && !e.this_device {
                            self.on_remote(&mut state, &e);
                        }
                        state.remote.push_back(e);
                    }
                    while state.remote.len() > KEEP {
                        state.remote.pop_front();
                    }
                    state.cursor = Some(answer.cursor);
                    if !answer.more && sent_all {
                        break;
                    }
                }
                Err(e) => {
                    let mut state = self.lock();
                    if matches!(e, ApiError::Status { status: 422, .. }) && request.after.is_some()
                    {
                        // A cursor this server did not make: start again from now.
                        state.cursor = None;
                    } else if state.offline_since_ms.is_none() {
                        state.offline_since_ms = Some(now_ms());
                        changed = true;
                    }
                    changed |= !state.others.is_empty();
                    state.others.clear();
                    break;
                }
            }
        }
        changed
    }

    fn on_remote(&self, state: &mut State, e: &RemoteEvent) {
        let at = epoch(&e.at).unwrap_or(0.0);
        if e.op == Op::Renamed || e.op == Op::Deleted {
            self.supersede(state, Some(&e.disk_id), &e.path);
        }
        if e.op != Op::Renamed || e.kind != TrashKind::Folder || e.by.you {
            return;
        }
        let Some(to) = e.to.as_deref() else { return };
        if now_s() - at > NOTIFY_WITHIN_S {
            return;
        }
        let folder = self.core.disk_folder(Some(&e.disk_id));
        let mine = state.local.iter().any(|l| {
            l.op == Op::Renamed
                && l.disk.as_deref() == Some(e.disk_id.as_str())
                && l.path == to
                && l.to.as_deref() == Some(e.path.as_str())
        });
        if mine {
            // A colleague undid this Mac's rename.
            self.add_notice(
                state,
                NoticeKind::RenamedBack {
                    disk: Some(e.disk_id.clone()),
                    path: to.to_string(),
                    to: e.path.clone(),
                    by: e.by.clone(),
                },
                folder,
            );
            (self.emit)(ActivityEvent::Alert(Alert::RenamedBack {
                by: e.by.name.clone(),
                from: base(&e.path).to_string(),
                to: base(to).to_string(),
            }));
            return;
        }
        if self.recently_made(state, Some(&e.disk_id), &e.path, at) {
            return;
        }
        self.add_notice(
            state,
            NoticeKind::Renamed {
                disk: Some(e.disk_id.clone()),
                path: e.path.clone(),
                to: to.to_string(),
                by: e.by.clone(),
                at,
            },
            folder,
        );
        (self.emit)(ActivityEvent::Alert(Alert::Renamed {
            by: e.by.clone(),
            from: base(&e.path).to_string(),
            to: base(to).to_string(),
        }));
    }

    /// The moment of the risk (DECISIONS #27): this Mac opened a file a
    /// colleague is still copying. One notification per folder, and one
    /// when the copies there are done.
    fn arrivals(&self) -> bool {
        let mut state = self.lock();
        let arriving = |state: &State, disk: &str, folder: &str| {
            state.others.iter().any(|w| {
                w.op == Op::Added && !w.by.you && w.disk_id == disk && parent(&w.path) == folder
            })
        };
        let mut alerts = Vec::new();
        let opened: Vec<(String, String)> = state
            .open
            .iter()
            .filter_map(|o| Some((o.disk.clone()?, o.path.clone())))
            .collect();
        for (disk, path) in opened {
            let key = (disk.clone(), parent(&path).to_string());
            if state.told.contains_key(&key) {
                continue;
            }
            let Some(w) = state
                .others
                .iter()
                .find(|w| w.op == Op::Added && !w.by.you && w.disk_id == disk && w.path == path)
            else {
                continue;
            };
            let by = w.by.name.clone();
            alerts.push(Alert::Arriving {
                by: by.clone(),
                file: base(&path).to_string(),
                folder: self.folder_name(&disk, &key.1),
                size: w.size,
            });
            state.told.insert(key, by);
        }
        let finished: Vec<(String, String)> = state
            .told
            .keys()
            .filter(|(d, f)| !arriving(&state, d, f))
            .cloned()
            .collect();
        for key in finished {
            if let Some(by) = state.told.remove(&key) {
                alerts.push(Alert::Arrived {
                    by,
                    folder: self.folder_name(&key.0, &key.1),
                });
            }
        }
        drop(state);
        let any = !alerts.is_empty();
        for a in alerts {
            (self.emit)(ActivityEvent::Alert(a));
        }
        any
    }

    fn folder_name(&self, disk: &str, folder: &str) -> String {
        if folder.is_empty() {
            self.core.disk_folder(Some(disk)).unwrap_or_default()
        } else {
            base(folder).to_string()
        }
    }

    fn save(&self) {
        let p = {
            let s = self.lock();
            ActivityPrefs {
                local_seen: s.seen,
                local_sent: s.local_sent,
                cursor: s.cursor.clone(),
                server: s.server.clone(),
            }
        };
        if self.prefs.load().activity != p {
            let _ = self.prefs.update(|prefs| prefs.activity = p);
        }
    }

    /// A page of a disk's activity, newest first; `disk` None is the
    /// single-user volume, which only this Mac writes.
    pub fn feed(&self, disk: Option<&str>, before: Option<&str>) -> Result<FeedPage, CoreError> {
        let (local, live, offline) = {
            let state = self.lock();
            let local: Vec<LocalEvent> = state
                .local
                .iter()
                .filter(|e| e.disk.as_deref() == disk)
                .cloned()
                .collect();
            (
                local,
                Self::live(&state, Some(disk)),
                state.offline_since_ms,
            )
        };
        let own_page = |before: Option<&str>| {
            let limit: u64 = before.and_then(|b| b.parse().ok()).unwrap_or(u64::MAX);
            let page: Vec<FeedEvent> = local
                .iter()
                .rev()
                .filter(|e| e.id < limit)
                .take(LOCAL_PAGE)
                .map(local_feed)
                .collect();
            let next = (page.len() == LOCAL_PAGE)
                .then(|| page.last().map(|e| e.id[1..].to_string()))
                .flatten();
            (page, next)
        };
        let (Some(d), Some(auth)) = (disk, self.session.device()) else {
            let (events, before) = own_page(before);
            return Ok(FeedPage {
                events,
                before,
                shared: false,
                offline_since_ms: None,
                live,
            });
        };
        match self
            .api
            .activity_history(&auth.server, &auth.token, d, before)
        {
            Ok(page) => {
                let mut events: Vec<FeedEvent> = Vec::new();
                if before.is_none() {
                    // This Mac's newest, not on the server yet.
                    let stored: HashSet<u64> = page
                        .events
                        .iter()
                        .filter(|e| e.this_device)
                        .map(|e| e.local_id)
                        .collect();
                    let sent = self.lock().local_sent;
                    events.extend(
                        local
                            .iter()
                            .rev()
                            .filter(|e| e.id > sent && !stored.contains(&e.id))
                            .map(local_feed),
                    );
                }
                events.extend(page.events.iter().map(remote_feed));
                Ok(FeedPage {
                    events,
                    before: page.before,
                    shared: true,
                    offline_since_ms: None,
                    live,
                })
            }
            Err(_) if before.is_none() => {
                let (events, _) = own_page(None);
                Ok(FeedPage {
                    events,
                    before: None,
                    shared: false,
                    offline_since_ms: offline.or_else(|| Some(now_ms())),
                    live,
                })
            }
            Err(e) => Err(CoreError::Server(e.to_string())),
        }
    }

    /// Fills in who deleted each trash entry this Mac did not, from the
    /// control plane; left unknown when it does not answer.
    pub fn who(&self, report: &mut TrashReport) {
        let Some(auth) = self.session.device() else {
            return;
        };
        for d in &mut report.disks {
            let Some(disk_id) = d.disk.clone() else {
                continue;
            };
            let asked: Vec<usize> = d
                .entries
                .iter()
                .enumerate()
                .filter(|(_, e)| !e.by_this_mac && e.from.is_some())
                .map(|(i, _)| i)
                .take(1000)
                .collect();
            if asked.is_empty() {
                continue;
            }
            let items = asked
                .iter()
                .map(|&i| {
                    let e = &d.entries[i];
                    let from = e.from.as_deref().unwrap_or_default();
                    DeleteQuery {
                        path: if from.is_empty() {
                            e.name.clone()
                        } else {
                            format!("{from}/{}", e.name)
                        },
                        at: e.deleted_at,
                    }
                })
                .collect();
            let req = DeletesRequest { disk_id, items };
            if let Ok(people) = self.api.activity_deletes(&auth.server, &auth.token, &req) {
                for (i, by) in asked.into_iter().zip(people) {
                    d.entries[i].by = by;
                }
            }
        }
    }

    /// Puts back what was deleted at `items`, from the trash: each to the
    /// entry of that path deleted closest to its time.
    pub fn restore_deleted(
        &self,
        disk: Option<&str>,
        items: &[DeletedItem],
    ) -> Result<RestoreView, CoreError> {
        let trash = self.core.trash()?;
        let Some(d) = trash.disks.iter().find(|d| d.disk.as_deref() == disk) else {
            return Err(CoreError::NoSuchDisk);
        };
        let mut ids = Vec::new();
        for item in items {
            let found = d
                .entries
                .iter()
                .filter(|e| {
                    let from = e.from.as_deref();
                    let path = match from {
                        Some("") => e.name.clone(),
                        Some(f) => format!("{f}/{}", e.name),
                        None => return false,
                    };
                    path == item.path && (e.deleted_at - item.at).abs() <= DELETE_WINDOW_S
                })
                .min_by(|a, b| {
                    (a.deleted_at - item.at)
                        .abs()
                        .total_cmp(&(b.deleted_at - item.at).abs())
                });
            if let Some(e) = found
                && !ids.contains(&e.id)
            {
                ids.push(e.id.clone());
            }
        }
        if ids.is_empty() {
            return Err(CoreError::NotInTrash);
        }
        self.core.restore(disk, &ids)
    }

    /// Gives `to` its name of before, `path`, never over another file.
    pub fn rename_back(
        &self,
        disk: Option<&str>,
        path: &str,
        to: &str,
    ) -> Result<Option<RenameBackRefusal>, CoreError> {
        self.core.vm_free()?;
        let done = engine::rename_back(self.core.engine(), disk, path, to)?;
        Ok(if done.done { None } else { done.reason })
    }

    /// Runs a notice's undo: the mass delete back from the trash, the
    /// rename undone.
    pub fn undo(&self, id: u64) -> Result<(), CoreError> {
        let kind = {
            let mut state = self.lock();
            let n = state
                .notices
                .iter_mut()
                .find(|n| n.id == id && n.can_undo)
                .ok_or(CoreError::NoSuchNotice)?;
            if n.state == NoticeState::Working || n.state == NoticeState::Done {
                return Err(CoreError::Busy);
            }
            n.state = NoticeState::Working;
            n.error = None;
            let kind = n.kind.clone();
            self.publish(&state);
            kind
        };
        let outcome: Result<Option<String>, String> = match &kind {
            NoticeKind::MassDelete { disk, items, .. } => {
                match self.restore_deleted(disk.as_deref(), items) {
                    Ok(view) => Ok(view.restored.first().and_then(|r| r.finder_path.clone())),
                    Err(CoreError::NotInTrash) => Err("not_in_trash".to_string()),
                    Err(e) => Err(e.to_string()),
                }
            }
            NoticeKind::Renamed { disk, path, to, .. } => {
                match self.rename_back(disk.as_deref(), path, to) {
                    Ok(None) => Ok(self.core.finder_path(disk.as_deref(), path)),
                    Ok(Some(refusal)) => Err(match refusal {
                        RenameBackRefusal::Gone => "gone",
                        RenameBackRefusal::Taken => "taken",
                        RenameBackRefusal::NoFolder => "no_folder",
                    }
                    .to_string()),
                    Err(e) => Err(e.to_string()),
                }
            }
            NoticeKind::RenamedBack { .. } => Ok(None),
        };
        let mut state = self.lock();
        if let Some(n) = state.notices.iter_mut().find(|n| n.id == id) {
            match outcome {
                Ok(finder) => {
                    n.state = NoticeState::Done;
                    n.finder_path = finder;
                }
                Err(reason) => {
                    n.state = NoticeState::Failed;
                    n.error = Some(reason);
                }
            }
        }
        self.publish(&state);
        Ok(())
    }

    /// Closes a notice.
    pub fn dismiss(&self, id: u64) {
        let mut state = self.lock();
        let before = state.notices.len();
        state.notices.retain(|n| n.id != id);
        if state.notices.len() != before {
            self.publish(&state);
        }
    }
}

#[cfg(test)]
mod tests;
