//! The activity against a fake `floppy` (its answers in files the tests
//! rewrite), a scripted control plane and a session that is signed in or not.

use super::*;
use crate::account::api::{ActivityAnswer, ActivityPage};
use crate::engine::tests::{DISK_A, disk_with, fake, team_status};
use std::path::PathBuf;

/// A control plane that answers from scripts and records what it was sent.
#[derive(Default)]
struct FakeApi {
    answers: Mutex<VecDeque<Result<ActivityAnswer, ApiError>>>,
    pages: Mutex<VecDeque<Result<ActivityPage, ApiError>>>,
    deletes: Mutex<Vec<Option<PersonRef>>>,
    sent: Mutex<Vec<ActivityRequest>>,
}

fn empty_answer(cursor: &str) -> ActivityAnswer {
    ActivityAnswer {
        events: vec![],
        cursor: cursor.into(),
        more: false,
        writing: vec![],
    }
}

impl ActivityApi for FakeApi {
    fn activity(
        &self,
        _: &str,
        _: &str,
        report: &ActivityRequest,
    ) -> Result<ActivityAnswer, ApiError> {
        self.sent.lock().unwrap().push(report.clone());
        self.answers
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| Ok(empty_answer("c")))
    }
    fn activity_history(
        &self,
        _: &str,
        _: &str,
        _: &str,
        _: Option<&str>,
    ) -> Result<ActivityPage, ApiError> {
        self.pages
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| Err(ApiError::Network("down".into())))
    }
    fn activity_deletes(
        &self,
        _: &str,
        _: &str,
        req: &DeletesRequest,
    ) -> Result<Vec<Option<PersonRef>>, ApiError> {
        let names = self.deletes.lock().unwrap();
        Ok((0..req.items.len())
            .map(|i| names.get(i).cloned().flatten())
            .collect())
    }
}

struct FakeSession(Option<DeviceAuth>);

impl Session for FakeSession {
    fn device(&self) -> Option<DeviceAuth> {
        self.0.clone()
    }
}

/// A disk this Mac writes.
fn writable() -> String {
    disk_with(
        DISK_A,
        &[("access", r#""write""#), ("mounted_access", r#""write""#)],
    )
}

struct Rig {
    dir: tempfile::TempDir,
    activity: Arc<Activity>,
    api: Arc<FakeApi>,
    events: Arc<Mutex<Vec<ActivityEvent>>>,
}

impl Rig {
    /// A team Mac with `disk` mounted, signed in when `signed_in`.
    fn new(disk: &str, signed_in: bool) -> Rig {
        let dir = tempfile::tempdir().unwrap();
        let status = team_status(&[disk], "");
        let d = dir.path().display();
        let bin = fake(
            dir.path(),
            &format!(
                r#"case "$1 $2" in
  "status --json") echo '{status}' ;;
  "activity --after") cat "{d}/activity.json" ;;
  "activity rename-back") cat > "{d}/rename-back.in"; cat "{d}/rename-back.json" ;;
  "trash ") cat "{d}/trash.json" ;;
  "trash restore") echo "$*" > "{d}/restore.args"; cat "{d}/restore.json" ;;
  *) exit 2 ;;
esac"#
            ),
        );
        let core = Core::new(bin, dir.path().join("home"), None, |_| {});
        core.refresh();
        let api = Arc::new(FakeApi::default());
        let events = Arc::new(Mutex::new(Vec::new()));
        let seen = Arc::clone(&events);
        let session = FakeSession(signed_in.then(|| DeviceAuth {
            server: "https://floppy.test".into(),
            token: "tok".into(),
        }));
        let activity = Activity::new(
            core,
            Arc::clone(&api) as Arc<dyn ActivityApi>,
            Arc::new(session),
            PrefsStore::new(&dir.path().join("prefs")),
            move |e| seen.lock().unwrap().push(e),
        );
        let rig = Rig {
            dir,
            activity,
            api,
            events,
        };
        rig.engine(&[], 0);
        rig
    }

    fn path(&self, name: &str) -> PathBuf {
        self.dir.path().join(name)
    }

    /// What the next `floppy activity` prints: these events, nothing written.
    fn engine(&self, events: &[serde_json::Value], last: u64) {
        self.engine_with(events, last, "[]", "[]");
    }

    fn engine_with(&self, events: &[serde_json::Value], last: u64, writing: &str, open: &str) {
        let body = format!(
            r#"{{"events": {}, "last": {last}, "writing": {writing}, "open": {open}}}"#,
            serde_json::Value::Array(events.to_vec())
        );
        std::fs::write(self.path("activity.json"), body).unwrap();
    }

    fn alerts(&self) -> Vec<Alert> {
        self.events
            .lock()
            .unwrap()
            .iter()
            .filter_map(|e| match e {
                ActivityEvent::Alert(a) => Some(a.clone()),
                ActivityEvent::Changed(_) => None,
            })
            .collect()
    }

    fn notices(&self) -> Vec<ActivityNotice> {
        self.activity.snapshot().notices
    }
}

fn now() -> f64 {
    now_s()
}

fn local(id: u64, disk: Option<&str>, op: &str, kind: &str, path: &str) -> serde_json::Value {
    let mut e = serde_json::json!({"id": id, "at": now() - 2.0, "disk": disk, "op": op, "kind": kind, "path": path});
    if op == "added" || op == "deleted" {
        e["size"] = serde_json::json!(1000);
    }
    e
}

fn renamed(id: u64, path: &str, to: &str) -> serde_json::Value {
    let mut e = local(id, Some("0192-a"), "renamed", "folder", path);
    e["to"] = serde_json::json!(to);
    e
}

fn iso(t: f64) -> String {
    // Seconds since the epoch, in the server's form.
    let secs = t.floor() as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // Civil date from days (inverse of `epoch`).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

fn remote(
    id: &str,
    op: Op,
    kind: TrashKind,
    path: &str,
    to: Option<&str>,
    by: &str,
) -> RemoteEvent {
    RemoteEvent {
        id: id.into(),
        disk_id: "0192-a".into(),
        at: iso(now() - 3.0),
        op,
        kind,
        path: path.into(),
        to: to.map(str::to_string),
        size: None,
        by: PersonRef {
            name: by.into(),
            you: false,
        },
        this_device: false,
        local_id: 1,
    }
}

fn answer(events: Vec<RemoteEvent>, writing: Vec<RemoteWriting>) -> ActivityAnswer {
    ActivityAnswer {
        events,
        cursor: "c2".into(),
        more: false,
        writing,
    }
}

#[test]
fn reads_server_times() {
    assert_eq!(epoch("1970-01-01T00:00:00Z"), Some(0.0));
    assert_eq!(epoch("2026-10-19T10:00:00.25Z"), Some(1_792_404_000.25));
    assert_eq!(epoch("2026-10-19T10:00:00+00:00"), Some(1_792_404_000.0));
    assert_eq!(epoch("2024-02-29T23:59:59Z"), Some(1_709_251_199.0));
    assert_eq!(epoch("2026-10-19T10:00:00+02:00"), None);
    assert_eq!(epoch("yesterday"), None);
    assert_eq!(epoch(&iso(1_792_404_000.0)), Some(1_792_404_000.0));
}

#[test]
fn what_was_there_before_the_app_never_notifies() {
    let rig = Rig::new(&writable(), true);
    rig.engine(&[renamed(5, "Backstage", "Backstage Tosca")], 5);
    rig.activity.tick();
    assert!(rig.notices().is_empty());
    // The first report starts the cursor and brings no events.
    assert_eq!(rig.api.sent.lock().unwrap()[0].after, None);
    // Signed in for the first time: the past stays off this server.
    assert!(rig.api.sent.lock().unwrap()[0].events.is_empty());

    rig.engine(&[renamed(6, "Clienti", "Clienti 2026")], 6);
    rig.activity.tick();
    let notices = rig.notices();
    assert_eq!(notices.len(), 1);
    assert!(
        matches!(&notices[0].kind, NoticeKind::Renamed { to, by, .. } if to == "Clienti 2026" && by.you)
    );
    assert!(notices[0].can_undo);
    assert_eq!(notices[0].folder.as_deref(), Some("Archivio 2025"));
    assert!(matches!(&rig.alerts()[0], Alert::Renamed { from, .. } if from == "Clienti"));
    // And the event went to the control plane, with the cursor.
    let sent = rig.api.sent.lock().unwrap();
    assert_eq!(sent[1].after.as_deref(), Some("c"));
    assert_eq!(sent[1].events.len(), 1);
    assert_eq!(sent[1].events[0].local_id, 6);
}

#[test]
fn the_single_user_volume_stays_on_this_mac() {
    let rig = Rig::new(&writable(), true);
    rig.activity.tick();
    rig.engine(
        &[
            local(1, None, "added", "file", "a.mov"),
            local(2, None, "added", "file", "b.mov"),
        ],
        2,
    );
    rig.activity.tick();
    assert!(
        rig.api
            .sent
            .lock()
            .unwrap()
            .iter()
            .all(|r| r.events.is_empty())
    );
    let page = rig.activity.feed(None, None).unwrap();
    assert_eq!(
        page.events
            .iter()
            .map(|e| e.path.as_str())
            .collect::<Vec<_>>(),
        ["b.mov", "a.mov"]
    );
    assert!(page.events[0].by.you && page.events[0].this_mac);
    assert!(!page.shared);
}

#[test]
fn a_colleague_renaming_a_folder_notifies_everyone_who_sees_the_disk() {
    let rig = Rig::new(DISK_A, true);
    rig.activity.tick();
    rig.api.answers.lock().unwrap().push_back(Ok(answer(
        vec![
            remote(
                "1",
                Op::Renamed,
                TrashKind::Folder,
                "Backstage",
                Some("Backstage Tosca"),
                "Marco",
            ),
            // A new folder taking its name is not news.
            remote(
                "2",
                Op::Created,
                TrashKind::Folder,
                "senza titolo",
                None,
                "Marco",
            ),
            remote(
                "3",
                Op::Renamed,
                TrashKind::Folder,
                "senza titolo",
                Some("Girato"),
                "Marco",
            ),
            remote(
                "4",
                Op::Renamed,
                TrashKind::File,
                "a.mov",
                Some("b.mov"),
                "Marco",
            ),
        ],
        vec![],
    )));
    rig.activity.tick();
    let notices = rig.notices();
    assert_eq!(notices.len(), 1);
    assert!(
        matches!(&notices[0].kind, NoticeKind::Renamed { by, path, .. } if by.name == "Marco" && path == "Backstage")
    );
    // Archivio 2025 is read-only here: told, not offered the undo.
    assert!(!notices[0].can_undo);
    assert_eq!(rig.alerts().len(), 1);

    // Renamed again by anyone: the undo no longer applies.
    rig.api.answers.lock().unwrap().push_back(Ok(answer(
        vec![remote(
            "5",
            Op::Renamed,
            TrashKind::Folder,
            "Backstage Tosca",
            Some("Tosca"),
            "Giulia",
        )],
        vec![],
    )));
    rig.activity.tick();
    let notices = rig.notices();
    assert_eq!(notices.len(), 1);
    assert!(matches!(&notices[0].kind, NoticeKind::Renamed { to, .. } if to == "Tosca"));
}

#[test]
fn undoing_a_rename_and_the_colleague_hears_of_it() {
    let rig = Rig::new(&writable(), true);
    rig.activity.tick();
    rig.api.answers.lock().unwrap().push_back(Ok(answer(
        vec![remote(
            "1",
            Op::Renamed,
            TrashKind::Folder,
            "Backstage",
            Some("Backstage Tosca"),
            "Marco",
        )],
        vec![],
    )));
    rig.activity.tick();
    let id = rig.notices()[0].id;
    std::fs::write(
        rig.path("rename-back.json"),
        r#"{"disk": "0192-a", "path": "Backstage", "to": "Backstage Tosca", "done": true, "reason": null}"#,
    )
    .unwrap();
    rig.activity.undo(id).unwrap();
    assert_eq!(
        std::fs::read_to_string(rig.path("rename-back.in")).unwrap(),
        r#"{"path":"Backstage","to":"Backstage Tosca"}"#
    );
    let n = &rig.notices()[0];
    assert_eq!(n.state, NoticeState::Done);
    assert_eq!(
        n.finder_path.as_deref(),
        Some("/Volumes/Floppy/Archivio 2025/Backstage")
    );
    // The engine then records this Mac's rename back: no notice of its own.
    rig.engine(&[renamed(1, "Backstage Tosca", "Backstage")], 1);
    rig.activity.tick();
    assert_eq!(rig.notices().len(), 1);

    // On Marco's side, the same event from the server.
    let marco = Rig::new(&writable(), true);
    marco.activity.tick();
    marco.engine(&[renamed(7, "Backstage", "Backstage Tosca")], 7);
    marco.activity.tick();
    marco.api.answers.lock().unwrap().push_back(Ok(answer(
        vec![remote(
            "9",
            Op::Renamed,
            TrashKind::Folder,
            "Backstage Tosca",
            Some("Backstage"),
            "Giulia",
        )],
        vec![],
    )));
    marco.activity.tick();
    let notices = marco.notices();
    assert_eq!(notices.len(), 1);
    assert!(matches!(&notices[0].kind, NoticeKind::RenamedBack { by, .. } if by.name == "Giulia"));
    assert!(
        marco
            .alerts()
            .iter()
            .any(|a| matches!(a, Alert::RenamedBack { from, to, .. } if from == "Backstage Tosca" && to == "Backstage"))
    );
}

#[test]
fn a_refused_undo_says_why() {
    let rig = Rig::new(&writable(), false);
    rig.activity.tick();
    rig.engine(&[renamed(1, "Backstage", "Backstage Tosca")], 1);
    rig.activity.tick();
    std::fs::write(
        rig.path("rename-back.json"),
        r#"{"disk": "0192-a", "path": "Backstage", "to": "x", "done": false, "reason": "taken"}"#,
    )
    .unwrap();
    let id = rig.notices()[0].id;
    rig.activity.undo(id).unwrap();
    let n = &rig.notices()[0];
    assert_eq!(
        (n.state, n.error.as_deref()),
        (NoticeState::Failed, Some("taken"))
    );
    rig.activity.dismiss(id);
    assert!(rig.notices().is_empty());
}

#[test]
fn a_folder_deleted_is_a_mass_delete_with_its_undo() {
    let rig = Rig::new(&writable(), false);
    rig.activity.tick();
    let mut events: Vec<serde_json::Value> = (1..=3)
        .map(|n| {
            local(
                n,
                Some("0192-a"),
                "deleted",
                "file",
                &format!("Pastificio/Girato grezzo/{n}.mov"),
            )
        })
        .collect();
    events.push(local(
        4,
        Some("0192-a"),
        "deleted",
        "folder",
        "Pastificio/Girato grezzo/Raw",
    ));
    events.push(local(
        5,
        Some("0192-a"),
        "deleted",
        "folder",
        "Pastificio/Girato grezzo",
    ));
    rig.engine(&events, 5);
    rig.activity.tick();
    // Still deleting, as far as the app knows.
    assert!(rig.notices().is_empty());
    rig.activity.settle_bursts(Instant::now() + SETTLE);
    let notices = rig.notices();
    let NoticeKind::MassDelete {
        items,
        name,
        files,
        bytes,
        ..
    } = &notices[0].kind
    else {
        panic!("{notices:?}")
    };
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].path, "Pastificio/Girato grezzo");
    assert_eq!(
        (name.as_deref(), *files, *bytes),
        (Some("Girato grezzo"), 3, 3000)
    );
    assert!(matches!(
        &rig.alerts()[0],
        Alert::MassDelete { files: 3, .. }
    ));

    let at = items[0].at;
    std::fs::write(
        rig.path("trash.json"),
        format!(
            r#"{{"disks": [{{"disk": "0192-a", "folder": "Archivio 2025", "writable": true, "trash_days": 7, "entries": [
            {{"id": "h/1-2", "name": "Girato grezzo", "kind": "folder", "from": "Pastificio", "deleted_at": {}, "expires_at": 0, "size": 3000, "replaced": false, "by_this_mac": true, "files": 3}},
            {{"id": "h/1-3", "name": "Girato grezzo", "kind": "folder", "from": "Pastificio", "deleted_at": {}, "expires_at": 0, "size": 1, "replaced": false, "by_this_mac": false, "files": 1}}]}}]}}"#,
            at + 0.5,
            at - 3600.0
        )
        .replace('\n', " "),
    )
    .unwrap();
    std::fs::write(
        rig.path("restore.json"),
        r#"{"restored": [{"id": "h/1-2", "disk": "0192-a", "path": "Pastificio/Girato grezzo", "name": "Girato grezzo", "renamed": false, "moved": false}], "failed": []}"#,
    )
    .unwrap();
    rig.activity.undo(notices[0].id).unwrap();
    // The entry of that delete, not the older one with the same name.
    assert_eq!(
        std::fs::read_to_string(rig.path("restore.args"))
            .unwrap()
            .trim(),
        "trash restore 0192-a h/1-2"
    );
    let n = &rig.notices()[0];
    assert_eq!(n.state, NoticeState::Done);
    assert_eq!(
        n.finder_path.as_deref(),
        Some("/Volumes/Floppy/Archivio 2025/Pastificio/Girato grezzo")
    );
}

#[test]
fn a_few_files_deleted_are_not_a_mass_delete() {
    let rig = Rig::new(&writable(), false);
    rig.activity.tick();
    let events: Vec<serde_json::Value> = (1..=MASS_FILES)
        .map(|n| local(n, Some("0192-a"), "deleted", "file", &format!("a/{n}.mov")))
        .collect();
    rig.engine(&events, MASS_FILES);
    rig.activity.tick();
    rig.activity.settle_bursts(Instant::now() + SETTLE);
    assert!(rig.notices().is_empty());
    // One more is: more than 20 files.
    let mut more = events.clone();
    more.push(local(
        MASS_FILES + 1,
        Some("0192-a"),
        "deleted",
        "file",
        "a/x.mov",
    ));
    let rig = Rig::new(&writable(), false);
    rig.activity.tick();
    rig.engine(&more, MASS_FILES + 1);
    rig.activity.tick();
    rig.activity.settle_bursts(Instant::now() + SETTLE);
    let notices = rig.notices();
    assert!(
        matches!(&notices[0].kind, NoticeKind::MassDelete { items, name: None, .. } if items.len() == 21)
    );
}

#[test]
fn opening_a_file_still_arriving_warns_once_per_folder() {
    let rig = Rig::new(&writable(), true);
    rig.activity.tick();
    let copying = |path: &str| RemoteWriting {
        disk_id: "0192-a".into(),
        path: path.into(),
        op: Op::Added,
        since: iso(now() - 64.0),
        size: Some(512_000_000),
        by: PersonRef {
            name: "Marco".into(),
            you: false,
        },
    };
    let open = r#"[{"disk": "0192-a", "path": "Cantine/Girato/A014.mov"}, {"disk": "0192-a", "path": "Cantine/Girato/A015.mov"}]"#;
    rig.engine_with(&[], 0, "[]", open);
    for _ in 0..2 {
        rig.api.answers.lock().unwrap().push_back(Ok(answer(
            vec![],
            vec![
                copying("Cantine/Girato/A014.mov"),
                copying("Cantine/Girato/A015.mov"),
            ],
        )));
        rig.activity.tick();
    }
    let alerts = rig.alerts();
    assert_eq!(alerts.len(), 1);
    assert!(
        matches!(&alerts[0], Alert::Arriving { by, folder, file, .. }
        if by == "Marco" && folder == "Girato" && file == "A014.mov")
    );
    // The panel shows the copy with its speed: 512 MB in about 64 s.
    let live = rig.activity.snapshot().live;
    assert_eq!(live.len(), 2);
    let speed = live[0].speed.unwrap();
    assert!((7_000_000.0..9_000_000.0).contains(&speed), "{speed}");

    rig.api
        .answers
        .lock()
        .unwrap()
        .push_back(Ok(answer(vec![], vec![])));
    rig.activity.tick();
    let alerts = rig.alerts();
    assert!(matches!(&alerts[1], Alert::Arrived { folder, .. } if folder == "Girato"));
    assert!(rig.activity.snapshot().live.is_empty());
}

#[test]
fn the_feed_falls_back_to_this_mac_without_the_control_plane() {
    let rig = Rig::new(&writable(), true);
    rig.activity.tick();
    rig.engine(&[local(1, Some("0192-a"), "added", "file", "a.mov")], 1);
    rig.api
        .answers
        .lock()
        .unwrap()
        .push_back(Err(ApiError::Network("down".into())));
    rig.activity.tick();
    let page = rig.activity.feed(Some("0192-a"), None).unwrap();
    assert!(!page.shared);
    assert!(page.offline_since_ms.is_some());
    assert_eq!(page.events.len(), 1);

    // Back: the server's page, with this Mac's event not stored yet on top.
    rig.api.pages.lock().unwrap().push_back(Ok(ActivityPage {
        events: vec![remote(
            "1",
            Op::Deleted,
            TrashKind::File,
            "b.mov",
            None,
            "Marco",
        )],
        before: Some("next".into()),
    }));
    let page = rig.activity.feed(Some("0192-a"), None).unwrap();
    assert!(page.shared);
    assert_eq!(
        page.events
            .iter()
            .map(|e| e.path.as_str())
            .collect::<Vec<_>>(),
        ["a.mov", "b.mov"]
    );
    assert_eq!(page.before.as_deref(), Some("next"));
}

#[test]
fn the_trash_learns_who_deleted_from_the_control_plane() {
    let rig = Rig::new(&writable(), true);
    *rig.api.deletes.lock().unwrap() = vec![Some(PersonRef {
        name: "Marco".into(),
        you: false,
    })];
    let mut report: TrashReport = serde_json::from_str(
        r#"{"disks": [{"disk": "0192-a", "folder": "Archivio 2025", "writable": true, "trash_days": 7, "entries": [
        {"id": "h/1-2", "name": "a.mov", "kind": "file", "from": "Girato", "deleted_at": 1.0, "expires_at": 0, "size": 1, "replaced": false, "by_this_mac": false},
        {"id": "h/1-3", "name": "b.mov", "kind": "file", "from": "Girato", "deleted_at": 1.0, "expires_at": 0, "size": 1, "replaced": false, "by_this_mac": true}]}]}"#,
    )
    .unwrap();
    rig.activity.who(&mut report);
    let entries = &report.disks[0].entries;
    assert_eq!(
        entries[0].by.as_ref().map(|p| p.name.as_str()),
        Some("Marco")
    );
    assert_eq!(entries[1].by, None);
}

#[test]
fn where_it_got_survives_a_restart() {
    let rig = Rig::new(&writable(), true);
    rig.activity.tick();
    rig.engine(&[local(3, Some("0192-a"), "added", "file", "a.mov")], 3);
    rig.activity.tick();
    let prefs = PrefsStore::new(&rig.path("prefs")).load().activity;
    assert_eq!(
        (prefs.local_seen, prefs.local_sent, prefs.cursor.as_deref()),
        (Some(3), 3, Some("c"))
    );
    assert_eq!(prefs.server.as_deref(), Some("https://floppy.test"));
}
