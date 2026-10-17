//! The sign-in against the real control plane of `server/` (its compose
//! stack, with `FLOPPY_MAIL_BACKEND=console`): codes and links are read from
//! the server container's log. Ignored by default; run with
//!
//! ```text
//! FLOPPY_E2E_SERVER=http://127.0.0.1:4507 cargo test e2e -- --ignored --test-threads=1
//! ```
//!
//! `FLOPPY_E2E_ADMIN` (default `admin@example.com`) is the bootstrap admin, who
//! invites a fresh member for each run and revokes its Mac;
//! `FLOPPY_E2E_CONTAINER` (default `floppy-server-server-1`) holds the log.

use super::*;
use crate::account::secrets::MemoryStore;
use std::process::Command;

fn env(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

/// The newest console mail to `to`, waiting for it to show up in the log.
fn mail_to(to: &str, after: usize) -> (String, usize) {
    let container = env("FLOPPY_E2E_CONTAINER", "floppy-server-server-1");
    let marker = format!("To: {to}\n");
    for _ in 0..50 {
        let out = Command::new("docker")
            .args(["logs", &container])
            .output()
            .expect("docker logs");
        let log = format!(
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
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

fn mails_so_far(to: &str) -> usize {
    let container = env("FLOPPY_E2E_CONTAINER", "floppy-server-server-1");
    let out = Command::new("docker")
        .args(["logs", &container])
        .output()
        .expect("docker logs");
    let log = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    log.matches(&format!("To: {to}\n")).count()
}

fn token_in(mail: &str) -> String {
    let at = mail.find("token=").expect("a link") + 6;
    mail[at..]
        .split(|c: char| c.is_whitespace())
        .next()
        .unwrap()
        .to_string()
}

fn code_in(mail: &str) -> String {
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

/// A web session cookie for `email`, by magic link.
fn web_session(server: &str, email: &str) -> String {
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
    cookie.split(';').next().unwrap().to_string()
}

fn account_for(server: &str, dir: &tempfile::TempDir) -> (Arc<Account>, Arc<MemoryStore>) {
    let prefs = PrefsStore::new(dir.path());
    prefs
        .update(|p| p.server_url = Some(server.to_string()))
        .unwrap();
    let store = Arc::new(MemoryStore::default());
    let account = Account::new(
        Arc::new(api::Http::new("Floppy/e2e")),
        Arc::clone(&store) as Arc<dyn SecretStore>,
        prefs,
        Versions {
            app_version: "0.1.0".into(),
            macos_version: "15.5".into(),
        },
        Timing {
            heartbeat: Duration::ZERO,
            ..Timing::default()
        },
        |_| {},
    );
    (account, store)
}

fn wait_for(account: &Account, what: &str, done: impl Fn(&Phase) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(20);
    while !done(&account.view().phase) {
        assert!(Instant::now() < deadline, "{what}: {:?}", account.view());
        thread::sleep(Duration::from_millis(100));
    }
}

#[test]
#[ignore]
fn e2e_against_the_local_stack() {
    let Ok(server) = std::env::var("FLOPPY_E2E_SERVER") else {
        panic!("set FLOPPY_E2E_SERVER, e.g. http://127.0.0.1:4507");
    };
    let server = server::normalize(&server).unwrap();
    let admin = web_session(&server, &env("FLOPPY_E2E_ADMIN", "admin@example.com"));
    let a = agent();

    // A fresh member for this run, invited and joined.
    let tag: String = pkce::random_token(9)
        .unwrap()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect();
    let email = format!("app-e2e-{}@example.com", tag.to_lowercase());
    let before = mails_so_far(&email);
    let r = a
        .post(server::endpoint(&server, "/people"))
        .header("Cookie", &admin)
        .send_json(serde_json::json!({ "email": email, "role": "member" }))
        .unwrap();
    assert_eq!(r.status(), 201, "invite");
    let (mail, _) = mail_to(&email, before);
    let r = agent()
        .post(server::endpoint(&server, "/auth/invitation"))
        .send_json(serde_json::json!({ "token": token_in(&mail) }))
        .unwrap();
    assert_eq!(r.status(), 200, "join");

    // 1. Code: a wrong one counts, the right one signs in and registers.
    let dir = tempfile::tempdir().unwrap();
    let (account, store) = account_for(&server, &dir);
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
    assert!(matches!(view.phase, Phase::SignedIn { .. }), "{view:?}");
    assert_eq!(store.names(), vec!["device", "disk-credentials"]);
    account.tick();
    assert!(account.view().reachable);

    // 2. An admin revokes it: the heartbeat signs the app out and clears the Keychain.
    let devices: serde_json::Value = a
        .get(server::endpoint(&server, "/devices"))
        .header("Cookie", &admin)
        .call()
        .unwrap()
        .body_mut()
        .read_json()
        .unwrap();
    let id = devices
        .as_array()
        .unwrap()
        .iter()
        .find(|d| d["name"] == "Mac e2e codice" && d["state"] == "active")
        .expect("the Mac in the admin's list")["id"]
        .as_str()
        .unwrap()
        .to_string();
    let r = a
        .post(server::endpoint(&server, &format!("/devices/{id}/revoke")))
        .header("Cookie", &admin)
        .send_empty()
        .unwrap();
    assert_eq!(r.status(), 200, "revoke");
    account.tick();
    assert_eq!(
        account.view().phase,
        Phase::SignedOut {
            notice: Some(OutNotice::Revoked)
        }
    );
    assert!(store.names().is_empty());

    // 3. The link, opened in a browser on this Mac, ends on the loopback.
    let dir = tempfile::tempdir().unwrap();
    let (account, store) = account_for(&server, &dir);
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
    wait_for(&account, "signed in by link", |p| {
        matches!(p, Phase::SignedIn { .. })
    });
    assert_eq!(store.names().len(), 2);

    // 4. Sign-out revokes it on the server and clears the Keychain.
    let token = {
        let bytes = store.get(DEVICE_ITEM).unwrap().unwrap();
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
    assert!(store.names().is_empty());
    let versions = Versions {
        app_version: "0.1.0".into(),
        macos_version: "15.5".into(),
    };
    let beat = api::Http::new("Floppy/e2e").heartbeat(&server, &token, &versions);
    assert_eq!(beat, Ok(Beat::Revoked), "the old token is revoked");
}
