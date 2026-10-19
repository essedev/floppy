//! A device as the app runs it, against a real control plane and the real
//! engine: the app's `Account` and `Core` with the `floppy` command of
//! `FLOPPY_BIN` (or `$FLOPPY_HOME/bin/floppy`), a real VM and the disks in the
//! Finder. Driven by `deploy/local/e2e.py`, which starts the local stack,
//! invites the member and changes its access in the web, and checks the Mac
//! side; this test signs the member in by code (read from the server's log, as
//! in `e2e.rs`), then lets the worker and the checker run as in the app,
//! printing one `E2E {json}` line on stdout for every change the driver waits
//! for, until the device is revoked and its disks are cleared. The app's
//! activity service runs too: its notices and notifications are printed, and
//! the driver asks for an undo, the trash or a restore by appending a line to
//! `FLOPPY_E2E_COMMANDS` (`undo <notice id>`, `trash`, `restore <disk> <path>
//! <time>`, `feed <disk>`). Ignored by default; it refuses the real
//! `~/.floppy`. Run with
//!
//! ```text
//! FLOPPY_HOME=/tmp/fe2e FLOPPY_VM=fe2e FLOPPY_E2E_SERVER=http://127.0.0.1:4583 \
//!   FLOPPY_E2E_CONTAINER=floppy-local-server-1 FLOPPY_E2E_EMAIL=<member> \
//!   cargo test e2e_engine -- --ignored --nocapture
//! ```
//!
//! The secrets stay in a `MemoryStore`, never the Keychain.

use super::e2e::{code_in, mail_to, mails_so_far};
use super::*;
use crate::account::secrets::MemoryStore;
use crate::activity::{Activity, ActivityEvent, DeletedItem};
use crate::core::Core;
use crate::engine;
use crate::team;
use std::time::Instant;

fn emit(event: &str, data: serde_json::Value) {
    println!(
        "E2E {}",
        serde_json::json!({ "event": event, "data": data })
    );
}

fn deadline() -> Duration {
    let minutes = std::env::var("FLOPPY_E2E_MINUTES")
        .ok()
        .and_then(|m| m.parse::<u64>().ok())
        .unwrap_or(20);
    Duration::from_secs(minutes * 60)
}

/// The disks file as the driver compares it: access per disk id.
fn file_view(home: &std::path::Path) -> serde_json::Value {
    match team::read(home) {
        Ok(Some(f)) => serde_json::json!({
            "meta_user": f.meta_user,
            "meta_ca": f.meta_ca.is_some(),
            "disks": f.disks.iter().map(|d| (d.id.clone(), serde_json::json!(d.access))).collect::<serde_json::Map<_, _>>(),
        }),
        Ok(None) => serde_json::Value::Null,
        Err(e) => serde_json::json!({ "error": e }),
    }
}

#[test]
#[ignore]
fn e2e_engine_device_against_the_local_stack() {
    let server =
        std::env::var("FLOPPY_E2E_SERVER").expect("FLOPPY_E2E_SERVER, e.g. http://127.0.0.1:4583");
    let server = server::normalize(&server).unwrap();
    let email = std::env::var("FLOPPY_E2E_EMAIL").expect("FLOPPY_E2E_EMAIL: a member who joined");
    let home = engine::floppy_home();
    let real = std::env::var_os("HOME").map(|h| std::path::PathBuf::from(h).join(".floppy"));
    assert!(
        std::env::var_os(engine::HOME_ENV).is_some() && Some(&home) != real.as_ref(),
        "set FLOPPY_HOME to a development home: this test never runs on the real install"
    );
    let bin = engine::engine_path();
    assert!(
        engine::is_executable(&bin),
        "no engine at {}",
        bin.display()
    );

    let core = Core::new(bin, home.clone(), None, |_| {});
    core.start_checking();
    let prefs_dir = tempfile::tempdir().unwrap();
    let prefs = PrefsStore::new(prefs_dir.path());
    prefs
        .update(|p| p.server_url = Some(server.clone()))
        .unwrap();
    let store = Arc::new(MemoryStore::default());
    let account = Account::new(
        Arc::new(api::Http::new("Floppy/e2e-engine")),
        Arc::clone(&store) as Arc<dyn SecretStore>,
        Arc::clone(&core) as Arc<dyn TeamDisks>,
        prefs,
        Versions {
            app_version: "0.1.0".into(),
            macos_version: "15.5".into(),
        },
        // The app's timing, with a faster heartbeat: access changes reach
        // the Mac in seconds instead of a minute.
        Timing {
            heartbeat: Duration::from_secs(3),
            config_retry: Duration::from_secs(5),
            clear_retry: Duration::from_secs(10),
            ..Timing::default()
        },
        |_| {},
    );

    // Sign-in by code, as typed in the app.
    let t = Instant::now();
    let before = mails_so_far(&email);
    let view = account.start(&email, "Mac e2e motore", false).unwrap();
    assert!(matches!(view.phase, Phase::CodeSent { .. }), "{view:?}");
    let (mail, _) = mail_to(&email, before);
    account.submit_code(&code_in(&mail)).unwrap();
    let signed_in = Instant::now() + Duration::from_secs(30);
    while !matches!(account.view().phase, Phase::SignedIn { .. }) {
        assert!(
            Instant::now() < signed_in,
            "never signed in: {:?}",
            account.view()
        );
        thread::sleep(Duration::from_millis(100));
    }
    assert_eq!(store.names(), vec!["device", "disk-credentials"]);
    emit(
        "signed_in",
        serde_json::json!({ "seconds": t.elapsed().as_secs_f64() }),
    );
    account.start_worker();

    let activity_prefs = tempfile::tempdir().unwrap();
    let activity = Activity::new(
        Arc::clone(&core),
        Arc::new(api::Http::new("Floppy/e2e-engine")),
        Arc::clone(&account) as Arc<dyn crate::activity::Session>,
        PrefsStore::new(activity_prefs.path()),
        |event| match event {
            ActivityEvent::Changed(s) => emit("notices", serde_json::to_value(&s.notices).unwrap()),
            ActivityEvent::Alert(a) => emit("alert", serde_json::to_value(&a).unwrap()),
        },
    );
    activity.start_worker();
    let commands = std::env::var("FLOPPY_E2E_COMMANDS").ok();
    let mut done_commands = 0;

    // The app at work: report every change of the disks file and of the
    // engine's view of the disks until the device is revoked.
    let end = Instant::now() + deadline();
    let (mut last_file, mut last_team, mut last_error) = (None, None, None);
    loop {
        let file = file_view(&home);
        if last_file.as_ref() != Some(&file) {
            emit("file", file.clone());
            last_file = Some(file);
        }
        let status = core.status();
        let team = serde_json::to_value(&status.team).unwrap();
        if last_team.as_ref() != Some(&team) {
            emit("team", team.clone());
            last_team = Some(team);
        }
        if last_error != status.engine_error {
            emit("engine_error", serde_json::json!(status.engine_error));
            last_error = status.engine_error.clone();
        }
        if let Some(path) = &commands {
            let text = std::fs::read_to_string(path).unwrap_or_default();
            let lines: Vec<&str> = text.lines().collect();
            for line in lines.iter().skip(done_commands) {
                command(&activity, &core, line);
            }
            done_commands = lines.len();
        }
        if let Phase::SignedOut { notice } = account.view().phase {
            assert_eq!(notice, Some(OutNotice::Revoked), "signed out, not revoked");
            break;
        }
        assert!(Instant::now() < end, "never revoked: {:?}", account.view());
        thread::sleep(Duration::from_millis(500));
    }

    // Revoked: the disks were cleared before the Keychain.
    assert!(store.names().is_empty(), "the secrets are gone");
    assert!(team::read(&home).unwrap().is_none(), "no disks file");
    let report = engine::status(core.engine()).unwrap();
    emit(
        "revoked",
        serde_json::json!({
            "disks": serde_json::to_value(report.team_disks()).unwrap(),
            "mount_point": report.mount_point,
        }),
    );
    assert!(report.team_disks().is_empty(), "the VM has no disk left");
}

/// One of the driver's commands; its outcome is printed as `E2E {"event": <verb>}`.
fn command(activity: &Activity, core: &Core, line: &str) {
    let parts: Vec<&str> = line.splitn(4, ' ').collect();
    match parts.as_slice() {
        ["undo", id] => {
            let out = activity.undo(id.parse().unwrap_or(0));
            emit(
                "undo",
                serde_json::json!({ "error": out.err().map(|e| e.to_string()) }),
            );
        }
        ["trash"] => {
            let out = core.trash().map(|mut r| {
                activity.who(&mut r);
                r
            });
            emit(
                "trash",
                match out {
                    Ok(r) => serde_json::to_value(r).unwrap(),
                    Err(e) => serde_json::json!({ "error": e.to_string() }),
                },
            );
        }
        ["restore", disk, at, path] => {
            let item = DeletedItem {
                path: path.to_string(),
                at: at.parse().unwrap_or(0.0),
            };
            let out = activity.restore_deleted(Some(disk), &[item]);
            emit(
                "restore",
                match out {
                    Ok(v) => serde_json::to_value(v).unwrap(),
                    Err(e) => serde_json::json!({ "error": e.to_string() }),
                },
            );
        }
        ["feed", disk] => {
            let out = activity.feed(Some(disk), None);
            emit(
                "feed",
                match out {
                    Ok(p) => serde_json::to_value(p).unwrap(),
                    Err(e) => serde_json::json!({ "error": e.to_string() }),
                },
            );
        }
        _ => emit("unknown_command", serde_json::json!(line)),
    }
}
