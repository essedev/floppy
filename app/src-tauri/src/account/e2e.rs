//! The sign-in and the team disks against the real control plane of
//! `server/` (its compose stack, with `FLOPPY_MAIL_BACKEND=console` and, for
//! disks without a bucket, the fake JuiceFS and storage backends): codes and
//! links are read from the server container's log. The engine is a fake
//! `floppy` in a temp dir driven by the real `Core`, so nothing touches this
//! Mac's install. Ignored by default; run with
//!
//! ```text
//! FLOPPY_E2E_SERVER=http://127.0.0.1:4557 FLOPPY_E2E_CONTAINER=floppy-app-e2e-server-1 \
//!   cargo test e2e -- --ignored --test-threads=1
//! ```
//!
//! `FLOPPY_E2E_ADMIN` (default `admin@example.com`) is the bootstrap admin, who
//! invites a fresh member for each run, gives it disks and revokes its Mac;
//! `FLOPPY_E2E_CONTAINER` (default `floppy-server-server-1`) holds the log.
//! The disks "Archivio 2025" and "Showreel 2026" are created on the first run
//! (manual mode with a placeholder bucket: the stack must use the fake
//! JuiceFS and storage backends) and reused after.

use super::*;
use crate::account::secrets::MemoryStore;
use crate::core::Core;
use crate::team;
use std::path::{Path, PathBuf};
use std::process::Command;

fn env(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn server_log() -> String {
    let container = env("FLOPPY_E2E_CONTAINER", "floppy-server-server-1");
    let out = Command::new("docker")
        .args(["logs", &container])
        .output()
        .expect("docker logs");
    format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    )
}

/// The newest console mail to `to`, waiting for it to show up in the log.
pub(super) fn mail_to(to: &str, after: usize) -> (String, usize) {
    let marker = format!("To: {to}\n");
    for _ in 0..50 {
        let log = server_log();
        let count = log.matches(&marker).count();
        if count > after {
            let at = log.rfind(&marker).unwrap();
            let body: String = log[at..].lines().take(14).collect::<Vec<_>>().join("\n");
            return (body, count);
        }
        thread::sleep(Duration::from_millis(200));
    }
    panic!("no mail to {to} in the log");
}

pub(super) fn mails_so_far(to: &str) -> usize {
    server_log().matches(&format!("To: {to}\n")).count()
}

fn token_in(mail: &str) -> String {
    let at = mail.find("token=").expect("a link") + 6;
    mail[at..]
        .split(|c: char| c.is_whitespace())
        .next()
        .unwrap()
        .to_string()
}

pub(super) fn code_in(mail: &str) -> String {
    mail.split(|c: char| !c.is_ascii_digit())
        .find(|w| w.len() == 6)
        .expect("a code")
        .to_string()
}

fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .http_status_as_error(false)
        .max_redirects(0)
        .build()
        .new_agent()
}

/// The admin's calls, with the web session's cookie.
struct Admin {
    server: String,
    cookie: String,
}

impl Admin {
    /// A web session for `email`, by magic link.
    fn sign_in(server: &str, email: &str) -> Admin {
        let a = agent();
        let before = mails_so_far(email);
        let r = a
            .post(server::endpoint(server, "/auth/sign-in"))
            .send_json(serde_json::json!({ "email": email }))
            .unwrap();
        assert_eq!(r.status(), 200);
        let (mail, _) = mail_to(email, before);
        let r = a
            .post(server::endpoint(server, "/auth/magic-link"))
            .send_json(serde_json::json!({ "token": token_in(&mail) }))
            .unwrap();
        assert_eq!(r.status(), 200, "admin sign-in");
        let cookie = r
            .headers()
            .get("set-cookie")
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        Admin {
            server: server.to_string(),
            cookie: cookie.split(';').next().unwrap().to_string(),
        }
    }

    fn call(
        &self,
        method: &str,
        path: &str,
        body: Option<serde_json::Value>,
    ) -> (u16, serde_json::Value) {
        let url = server::endpoint(&self.server, path);
        let a = agent();
        let mut r = match (method, body) {
            ("GET", _) => a.get(&url).header("Cookie", &self.cookie).call().unwrap(),
            ("PUT", Some(b)) => a
                .put(&url)
                .header("Cookie", &self.cookie)
                .send_json(b)
                .unwrap(),
            (_, Some(b)) => a
                .post(&url)
                .header("Cookie", &self.cookie)
                .send_json(b)
                .unwrap(),
            (_, None) => a
                .post(&url)
                .header("Cookie", &self.cookie)
                .send_empty()
                .unwrap(),
        };
        let status = r.status().as_u16();
        let json = r.body_mut().read_json().unwrap_or(serde_json::Value::Null);
        (status, json)
    }

    /// The id of the ready disk `name`, created on the first run.
    fn disk(&self, name: &str, bucket: &str) -> String {
        let find = || {
            let (_, disks) = self.call("GET", "/disks", None);
            disks
                .as_array()
                .unwrap()
                .iter()
                .find(|d| d["name"] == name)
                .cloned()
        };
        if find().is_none() {
            let (status, _) = self.call(
                "POST",
                "/disks",
                Some(serde_json::json!({"name": name, "storage": {
                    "endpoint": "https://s3.example.invalid", "region": "eu-south",
                    "bucket": bucket, "accessKeyId": "AKIAE2EAPPTEST", "secretKey": "secretE2Eapptest1"}})),
            );
            assert_eq!(status, 201, "create {name}");
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            let d = find().unwrap();
            if d["state"] == "ready" {
                return d["id"].as_str().unwrap().to_string();
            }
            assert!(Instant::now() < deadline, "{name} never ready: {d}");
            thread::sleep(Duration::from_millis(300));
        }
    }

    fn set_access(&self, disk: &str, person: &str, access: &str) {
        let (status, _) = self.call(
            "PUT",
            &format!("/disks/{disk}/access/{person}"),
            Some(serde_json::json!({ "access": access })),
        );
        assert_eq!(status, 200, "access {access}");
    }

    /// The Mac by its Valkey user (names repeat across runs).
    fn device(&self, valkey_user: &str) -> serde_json::Value {
        let (_, devices) = self.call("GET", "/devices", None);
        devices
            .as_array()
            .unwrap()
            .iter()
            .find(|d| d["valkeyUser"] == valkey_user)
            .cloned()
            .expect("the Mac in the admin's list")
    }
}

/// A fake `floppy` with the team version's contract: `status --json` from
/// `status.json`, `disks apply` keeping its stdin in `stdin`, `disks clear`
/// removing the disks file.
fn fake_engine(dir: &Path) -> PathBuf {
    let d = dir.display();
    let script = format!(
        r#"#!/bin/bash
D="{d}"
echo "$*" >> "$D/calls"
case "$1 ${{2:-}}" in
  "status --json") cat "$D/status.json" ;;
  "disks apply") cat > "$D/stdin"
    echo '{{"version":1,"mount_point":"/Volumes/Floppy","cache_per_disk_mb":10240,"vm_memory_mib":1024,"vm_memory_wanted_mib":1024,"restart_needed":false,"vm":{{"smbd":"active","drive":"active","disks":[]}}}}' ;;
  "disks clear") rm -f "$D/home/disks.json"; echo '{{"version":1,"cleared":true}}' ;;
  *) exit 2 ;;
esac
"#
    );
    let bin = dir.join("floppy");
    std::fs::write(&bin, script).unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
    bin
}

/// The team version's status with `mounted` disks mounted read-write.
fn status_with(dir: &Path, mounted: &[(&str, &str)]) {
    let disks: Vec<String> = mounted
        .iter()
        .map(|(id, folder)| {
            format!(
                r#"{{"id":"{id}","folder":"{folder}","access":"write","enabled":true,"state":"mounted","mounted_access":"write","pending":null,"cache_used_mb":1,"cache_size_mb":10240,"replica":"up","mode":"replica","online":true,"lag_ms":500,"listed":true}}"#
            )
        })
        .collect();
    let configured = dir.join("home/disks.json").exists();
    std::fs::write(
        dir.join("status.json"),
        format!(
            r#"{{"version":1,"mode":"team","configured":{configured},"installed":true,"stopped":false,"vm":"running","mount_point":"/Volumes/Floppy","responding":true,"drive":"active","cache_used_mb":1,"cache_limit_mb":30720,"mac_free_mb":200000,"reserve_mb":20480,"vm_memory_mib":1024,"vm_memory_wanted_mib":1024,"disks":[{}]}}"#,
            disks.join(",")
        ),
    )
    .unwrap();
}

struct Mac {
    account: Arc<Account>,
    core: Arc<Core>,
    store: Arc<MemoryStore>,
    dir: tempfile::TempDir,
}

impl Mac {
    fn new(server: &str) -> Mac {
        let dir = tempfile::tempdir().unwrap();
        let home = dir.path().join("home");
        std::fs::create_dir_all(&home).unwrap();
        status_with(dir.path(), &[]);
        let core = Core::new(fake_engine(dir.path()), home, None, |_| {});
        core.start_checking();
        let prefs = PrefsStore::new(dir.path());
        prefs
            .update(|p| p.server_url = Some(server.to_string()))
            .unwrap();
        let store = Arc::new(MemoryStore::default());
        let account = Account::new(
            Arc::new(api::Http::new("Floppy/e2e")),
            Arc::clone(&store) as Arc<dyn SecretStore>,
            Arc::clone(&core) as Arc<dyn TeamDisks>,
            prefs,
            Versions {
                app_version: "0.1.0".into(),
                macos_version: "15.5".into(),
            },
            Timing {
                heartbeat: Duration::ZERO,
                config_retry: Duration::ZERO,
                ..Timing::default()
            },
            |_| {},
        );
        Mac {
            account,
            core,
            store,
            dir,
        }
    }

    fn file(&self) -> Option<serde_json::Value> {
        std::fs::read(self.dir.path().join("home/disks.json"))
            .ok()
            .map(|b| serde_json::from_slice(&b).unwrap())
    }

    fn calls(&self) -> String {
        std::fs::read_to_string(self.dir.path().join("calls")).unwrap_or_default()
    }

    /// Waits until the disks file says `check`, ticking the account.
    fn wait_file(
        &self,
        what: &str,
        check: impl Fn(&serde_json::Value) -> bool,
    ) -> serde_json::Value {
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if let Some(f) = self.file().filter(|f| check(f)) {
                return f;
            }
            assert!(Instant::now() < deadline, "{what}: {:?}", self.file());
            self.account.tick();
            thread::sleep(Duration::from_millis(150));
        }
    }
}

fn wait_for(account: &Account, what: &str, done: impl Fn(&Phase) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(20);
    while !done(&account.view().phase) {
        assert!(Instant::now() < deadline, "{what}: {:?}", account.view());
        thread::sleep(Duration::from_millis(100));
    }
}

fn access_of(file: &serde_json::Value, id: &str) -> Option<String> {
    file["disks"]
        .as_array()?
        .iter()
        .find(|d| d["id"] == id)
        .map(|d| d["access"].as_str().unwrap().to_string())
}

#[test]
#[ignore]
fn e2e_against_the_local_stack() {
    let Ok(server) = std::env::var("FLOPPY_E2E_SERVER") else {
        panic!("set FLOPPY_E2E_SERVER, e.g. http://127.0.0.1:4557");
    };
    let server = server::normalize(&server).unwrap();
    let admin = Admin::sign_in(&server, &env("FLOPPY_E2E_ADMIN", "admin@example.com"));
    let archivio = admin.disk("Archivio 2025", "e2e-app-archivio");
    let showreel = admin.disk("Showreel 2026", "e2e-app-showreel");

    // A fresh member for this run, invited with read on one disk, and joined.
    let tag: String = pkce::random_token(9)
        .unwrap()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect();
    let email = format!("app-e2e-{}@example.com", tag.to_lowercase());
    let before = mails_so_far(&email);
    let (status, person) = admin.call(
        "POST",
        "/people",
        Some(serde_json::json!({ "email": email, "role": "member",
            "disks": [{"diskId": archivio, "access": "read"}] })),
    );
    assert_eq!(status, 201, "invite");
    let person = person["id"].as_str().unwrap().to_string();
    let (mail, _) = mail_to(&email, before);
    let r = agent()
        .post(server::endpoint(&server, "/auth/invitation"))
        .send_json(serde_json::json!({ "token": token_in(&mail) }))
        .unwrap();
    assert_eq!(r.status(), 200, "join");

    // 1. Code: a wrong one counts, the right one signs in and registers.
    let mac = Mac::new(&server);
    let account = &mac.account;
    let before = mails_so_far(&email);
    let view = account.start(&email, "Mac e2e codice", false).unwrap();
    assert!(matches!(view.phase, Phase::CodeSent { .. }), "{view:?}");
    let (mail, _) = mail_to(&email, before);
    let view = account.submit_code("000000").unwrap();
    assert_eq!(
        view.error,
        Some(AuthError::InvalidCode {
            attempts_left: Some(4)
        }),
        "the server would accept 000000 only by chance"
    );
    let view = account.submit_code(&code_in(&mail)).unwrap();
    let Phase::SignedIn {
        organization,
        user_name,
        ..
    } = &view.phase
    else {
        panic!("{view:?}");
    };
    assert!(organization.as_deref().is_some_and(|o| !o.is_empty()));
    assert!(
        user_name.as_deref().is_some_and(|n| !n.is_empty()),
        "the person's name"
    );
    assert_eq!(mac.store.names(), vec!["device", "disk-credentials"]);

    // The registration's disk is in the file, read-only, its secrets on stdin.
    let file = mac.wait_file("the first disk", |f| access_of(f, &archivio).is_some());
    assert_eq!(access_of(&file, &archivio).as_deref(), Some("read"));
    assert!(access_of(&file, &showreel).is_none(), "no access, no disk");
    let meta_user = file["meta_user"].as_str().unwrap().to_string();
    assert!(meta_user.starts_with("floppy_d_"));
    let disk = file["disks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|d| d["id"] == archivio.as_str())
        .unwrap();
    assert_eq!(disk["folder"], "Archivio 2025");
    assert!(team::valid_meta_url(disk["meta_url"].as_str().unwrap()));
    let raw = std::fs::read_to_string(mac.dir.path().join("home/disks.json")).unwrap();
    let stdin: serde_json::Value =
        serde_json::from_slice(&std::fs::read(mac.dir.path().join("stdin")).unwrap()).unwrap();
    let password = stdin["meta_password"].as_str().unwrap();
    assert!(password.len() >= 16);
    assert!(!raw.contains(password), "no secret in the disks file");
    assert!(!mac.calls().contains(password), "no secret in argv");

    // 2. The heartbeat reports the mount; the admin sees it.
    status_with(mac.dir.path(), &[(&archivio, "Archivio 2025")]);
    mac.core.refresh();
    account.tick();
    assert!(account.view().reachable);
    let device = admin.device(&meta_user);
    assert_eq!(device["replica"]["mode"], "replica", "{device}");
    assert_eq!(device["replica"]["lagMs"], 500, "{device}");

    // 3. Access raised on one disk, a second disk given: the heartbeat sees
    //    it, the configuration is read again and applied.
    admin.set_access(&archivio, &person, "write");
    admin.set_access(&showreel, &person, "read");
    let file = mac.wait_file("raised and given", |f| {
        access_of(f, &archivio).as_deref() == Some("write")
            && access_of(f, &showreel).as_deref() == Some("read")
    });
    assert_eq!(file["disks"].as_array().unwrap().len(), 2);
    // The configuration read after the registration: the same user, the CA
    // the server has (none on this stack).
    assert_eq!(file["meta_user"], meta_user.as_str());
    assert_eq!(file["meta_ca"], serde_json::Value::Null);
    // Taken away: gone from the file.
    admin.set_access(&showreel, &person, "none");
    mac.wait_file("taken away", |f| access_of(f, &showreel).is_none());

    // 4. An admin revokes it: the disks are cleared, then the Keychain.
    let id = admin.device(&meta_user)["id"].as_str().unwrap().to_string();
    let (status, _) = admin.call("POST", &format!("/devices/{id}/revoke"), None);
    assert_eq!(status, 200, "revoke");
    account.tick();
    assert_eq!(
        account.view().phase,
        Phase::SignedOut {
            notice: Some(OutNotice::Revoked)
        }
    );
    assert!(mac.calls().contains("disks clear"));
    assert!(mac.file().is_none());
    assert!(mac.store.names().is_empty());

    // 5. The link, opened in a browser on this Mac, ends on the loopback.
    let mac = Mac::new(&server);
    let account = &mac.account;
    let before = mails_so_far(&email);
    account.start(&email, "Mac e2e link", false).unwrap();
    let (mail, _) = mail_to(&email, before);
    let r = agent()
        .post(server::endpoint(&server, "/auth/app/link/continue"))
        .content_type("application/x-www-form-urlencoded")
        .send(format!("token={}", token_in(&mail)))
        .unwrap();
    assert_eq!(r.status(), 303, "the link's button");
    let loopback = r
        .headers()
        .get("location")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert!(loopback.starts_with("http://127.0.0.1:"));
    let page = agent().get(&loopback).call().unwrap();
    assert_eq!(page.status(), 200);
    wait_for(account, "signed in by link", |p| {
        matches!(p, Phase::SignedIn { .. })
    });
    assert_eq!(mac.store.names().len(), 2);
    mac.wait_file("the disk again", |f| access_of(f, &archivio).is_some());

    // 6. Sign-out revokes it on the server, clears the disks and the Keychain.
    let token = {
        let bytes = mac.store.get(DEVICE_ITEM).unwrap().unwrap();
        serde_json::from_slice::<DeviceRecord>(&bytes)
            .unwrap()
            .device_token
    };
    let view = account.sign_out(false).unwrap();
    assert_eq!(
        view.phase,
        Phase::SignedOut {
            notice: Some(OutNotice::SignedOut)
        }
    );
    assert_eq!(view.error, None);
    assert!(mac.calls().contains("disks clear"));
    assert!(mac.store.names().is_empty());

    // 7. The 401 reasons as the server words them.
    let http = api::Http::new("Floppy/e2e");
    let beat = HeartbeatRequest {
        versions: Versions {
            app_version: "0.1.0".into(),
            macos_version: "15.5".into(),
        },
        replica: None,
        s3_access_key_tail: None,
        mounted_disk_ids: vec![],
    };
    assert_eq!(
        http.heartbeat(&server, &token, &beat).unwrap().state,
        Beat::Revoked,
        "the old token is revoked"
    );
    let refusal = |token: &str| http.config(&server, token).unwrap_err().refusal();
    assert_eq!(refusal(&token), Some(api::DeviceRefusal::SignedOut));
    assert_eq!(
        refusal("not-a-token-this-server-knows"),
        Some(api::DeviceRefusal::TokenUnknown)
    );
}
