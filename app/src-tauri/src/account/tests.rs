//! The sign-in state machine against a scripted control plane, a Keychain in
//! memory and a recording engine side. The loopback test talks to the real
//! listener over TCP.

use super::*;
use crate::account::api::{Grant, HeartbeatAnswer, RegisterResponse, StartResponse};
use crate::account::secrets::MemoryStore;
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::atomic::Ordering;

type Script<T> = Mutex<VecDeque<Result<T, ApiError>>>;

/// A control plane that answers from scripts and records what it was sent.
#[derive(Default)]
struct Fake {
    start: Script<StartResponse>,
    status: Script<String>,
    exchange: Script<Grant>,
    register: Script<RegisterResponse>,
    config: Script<DeviceConfig>,
    heartbeat: Script<HeartbeatAnswer>,
    sign_out: Script<()>,
    beats: Mutex<Vec<HeartbeatRequest>>,
    starts: Mutex<Vec<StartRequest>>,
    exchanges: Mutex<Vec<(String, Proof)>>,
    tokens: Mutex<Vec<String>>,
}

fn next<T>(script: &Script<T>, what: &str) -> Result<T, ApiError> {
    script
        .lock()
        .unwrap()
        .pop_front()
        .unwrap_or_else(|| Err(ApiError::Network(format!("no scripted answer for {what}"))))
}

impl ControlPlane for Fake {
    fn start_sign_in(&self, _: &str, req: &StartRequest) -> Result<StartResponse, ApiError> {
        self.starts.lock().unwrap().push(req.clone());
        next(&self.start, "start")
    }
    fn sign_in_status(&self, _: &str, _: &str) -> Result<String, ApiError> {
        next(&self.status, "status")
    }
    fn exchange(&self, _: &str, verifier: &str, proof: &Proof) -> Result<Grant, ApiError> {
        self.exchanges
            .lock()
            .unwrap()
            .push((verifier.to_string(), proof.clone()));
        next(&self.exchange, "exchange")
    }
    fn register(&self, _: &str, token: &str, _: &Versions) -> Result<RegisterResponse, ApiError> {
        self.tokens.lock().unwrap().push(token.to_string());
        next(&self.register, "register")
    }
    fn config(&self, _: &str, token: &str) -> Result<DeviceConfig, ApiError> {
        self.tokens.lock().unwrap().push(token.to_string());
        next(&self.config, "config")
    }
    fn heartbeat(
        &self,
        _: &str,
        token: &str,
        beat: &HeartbeatRequest,
    ) -> Result<HeartbeatAnswer, ApiError> {
        self.tokens.lock().unwrap().push(token.to_string());
        self.beats.lock().unwrap().push(beat.clone());
        next(&self.heartbeat, "heartbeat")
    }
    fn sign_out(&self, _: &str, token: &str) -> Result<(), ApiError> {
        self.tokens.lock().unwrap().push(token.to_string());
        next(&self.sign_out, "sign-out")
    }
}

fn push<T>(script: &Script<T>, answer: Result<T, ApiError>) {
    script.lock().unwrap().push_back(answer);
}

fn status(status: u16, code: &str) -> ApiError {
    ApiError::Status {
        status,
        code: code.into(),
        message: code.into(),
        attempts_left: None,
        retry_after: None,
        reason: None,
    }
}

/// A device's 401 with its `details.reason`.
fn refused_for(reason: &str) -> ApiError {
    ApiError::Status {
        status: 401,
        code: "unauthorized".into(),
        message: reason.into(),
        attempts_left: None,
        retry_after: None,
        reason: Some(reason.into()),
    }
}

/// What the engine side was asked, and what the Keychain held at the clear.
#[derive(Default)]
struct FakeTeam {
    configured: Mutex<Vec<(DeviceConfig, DiskCredentials)>>,
    /// The Keychain's items at each clear: the disks go first.
    clears: Mutex<Vec<Vec<String>>>,
    clear_fails: AtomicBool,
    store: Mutex<Option<Arc<MemoryStore>>>,
    mounts: Mutex<MountReport>,
}

impl TeamDisks for FakeTeam {
    fn configure(&self, config: &DeviceConfig, credentials: &DiskCredentials) {
        self.configured
            .lock()
            .unwrap()
            .push((config.clone(), credentials.clone()));
    }
    fn report(&self) -> MountReport {
        self.mounts.lock().unwrap().clone()
    }
    fn clear(&self) -> Result<(), String> {
        let names = self
            .store
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.names())
            .unwrap_or_default();
        self.clears.lock().unwrap().push(names);
        if self.clear_fails.load(Ordering::SeqCst) {
            Err("the floppy command failed: the VM is not responding".into())
        } else {
            Ok(())
        }
    }
}

fn started(id: &str) -> Result<StartResponse, ApiError> {
    Ok(StartResponse {
        request_id: id.into(),
        email: "giulia@studio.test".into(),
        expires_at: "2026-10-17T20:30:00Z".into(),
        resend_at: "2026-10-17T20:16:00Z".into(),
    })
}

fn granted() -> Result<Grant, ApiError> {
    Ok(Grant {
        device_id: "dev-1".into(),
        device_token: "tok-secret".into(),
    })
}

const REGISTERED: &str = r#"{"valkeyUsername":"floppy_d_1","valkeyPassword":"pw-secret","storageMode":"manual","s3":null,
"organization":{"id":"o1","name":"Studio Esempio"},"user":{"email":"giulia@studio.test","name":"Giulia Ferri"},
"disks":[{"id":"0192-a","name":"Archivio 2025","volumeName":"archivio-2025","access":"read",
"metaUrl":"rediss://archivio-2025.meta.example.com:6443/1","cacheExpireDays":30,"mountPath":"/Volumes/Floppy/Archivio 2025"}]}"#;

fn answer() -> Result<RegisterResponse, ApiError> {
    Ok(serde_json::from_str(REGISTERED).unwrap())
}

/// The configuration as `GET /device/config` gives it, with `disks` changed.
fn config_with(disks: &[(&str, api::Access)]) -> DeviceConfig {
    let mut c = serde_json::from_str::<RegisterResponse>(REGISTERED)
        .unwrap()
        .config();
    let template = c.disks[0].clone();
    c.disks = disks
        .iter()
        .map(|(id, access)| api::DiskConfig {
            id: id.to_string(),
            access: *access,
            ..template.clone()
        })
        .collect();
    c
}

fn beat(state: &str, disks: &[(&str, &str)]) -> Result<HeartbeatAnswer, ApiError> {
    let disks: Vec<serde_json::Value> = disks
        .iter()
        .map(|(id, access)| serde_json::json!({"id": id, "access": access}))
        .collect();
    Ok(serde_json::from_value(serde_json::json!({"state": state, "disks": disks})).unwrap())
}

fn quick() -> Timing {
    Timing {
        tick: Duration::from_millis(10),
        poll: Duration::ZERO,
        elsewhere_after: Duration::from_millis(50),
        request_ttl: Duration::from_secs(30),
        register_retry: Duration::ZERO,
        heartbeat: Duration::ZERO,
        config_retry: Duration::ZERO,
        clear_retry: Duration::ZERO,
    }
}

struct Rig {
    account: Arc<Account>,
    fake: Arc<Fake>,
    team: Arc<FakeTeam>,
    store: Arc<MemoryStore>,
    events: Arc<Mutex<Vec<AccountEvent>>>,
    _dir: tempfile::TempDir,
}

fn rig_with(store: Arc<MemoryStore>) -> Rig {
    let fake = Arc::new(Fake::default());
    let team = Arc::new(FakeTeam::default());
    *team.store.lock().unwrap() = Some(Arc::clone(&store));
    let dir = tempfile::tempdir().unwrap();
    let events = Arc::new(Mutex::new(Vec::new()));
    let sink = Arc::clone(&events);
    let account = Account::new(
        Arc::clone(&fake) as Arc<dyn ControlPlane>,
        Arc::clone(&store) as Arc<dyn SecretStore>,
        Arc::clone(&team) as Arc<dyn TeamDisks>,
        PrefsStore::new(dir.path()),
        Versions {
            app_version: "0.1.0".into(),
            macos_version: "15.5".into(),
        },
        quick(),
        move |e| sink.lock().unwrap().push(e),
    );
    Rig {
        account,
        fake,
        team,
        store,
        events,
        _dir: dir,
    }
}

fn rig() -> Rig {
    rig_with(Arc::new(MemoryStore::default()))
}

/// A Keychain holding a signed-in Mac, as a previous launch left it.
fn signed_in_store(registered: bool) -> Arc<MemoryStore> {
    let store = Arc::new(MemoryStore::default());
    let record = DeviceRecord {
        server: "https://floppy.studio.test".into(),
        device_id: "dev-1".into(),
        device_token: "tok-secret".into(),
        email: "giulia@studio.test".into(),
        device_name: "MacBook di Giulia".into(),
        registered,
        organization: None,
        user_name: None,
    };
    store
        .set(DEVICE_ITEM, &serde_json::to_vec(&record).unwrap())
        .unwrap();
    let creds = DiskCredentials::from_register(&answer().unwrap());
    credentials::keep(store.as_ref(), &creds).unwrap();
    store
}

fn start(r: &Rig) -> AuthView {
    push(&r.fake.start, started("req-1"));
    r.account
        .start(" Giulia@Studio.test ", "MacBook di Giulia", false)
        .unwrap()
}

#[test]
fn signs_in_by_code_and_keeps_secrets_in_the_keychain_only() {
    let r = rig();
    let view = start(&r);
    assert!(
        matches!(view.phase, Phase::CodeSent { ref email, .. } if email == "giulia@studio.test")
    );
    let sent = r.fake.starts.lock().unwrap()[0].clone();
    assert_eq!(sent.email, "giulia@studio.test");
    assert_eq!(sent.device_name, "MacBook di Giulia");
    assert!(sent.redirect_uri.starts_with("http://127.0.0.1:"));

    push(&r.fake.exchange, granted());
    push(&r.fake.register, answer());
    let view = r.account.submit_code("123 456").unwrap();
    assert!(matches!(view.phase, Phase::SignedIn { .. }), "{view:?}");
    assert_eq!(view.busy, None);

    let (verifier, proof) = r.fake.exchanges.lock().unwrap()[0].clone();
    assert_eq!(proof, Proof::Code("123456".into()));
    assert_eq!(pkce::challenge(&verifier), sent.code_challenge);
    assert_eq!(r.fake.tokens.lock().unwrap()[0], "tok-secret");
    assert_eq!(r.store.names(), vec!["device", "disk-credentials"]);

    // What the windows receive never carries a secret.
    for e in r.events.lock().unwrap().iter() {
        if let AccountEvent::Changed(v) = e {
            let json = serde_json::to_string(v).unwrap();
            assert!(!json.contains("tok-secret") && !json.contains("pw-secret"));
            assert!(!json.contains(&verifier));
        }
    }
}

#[test]
fn a_wrong_code_counts_down_and_the_fifth_locks() {
    let r = rig();
    start(&r);
    push(
        &r.fake.exchange,
        Err(ApiError::Status {
            status: 422,
            code: "invalid_code".into(),
            message: "Wrong code".into(),
            attempts_left: Some(4),
            retry_after: None,
            reason: None,
        }),
    );
    let view = r.account.submit_code("000000").unwrap();
    assert!(matches!(view.phase, Phase::CodeSent { .. }));
    assert_eq!(
        view.error,
        Some(AuthError::InvalidCode {
            attempts_left: Some(4)
        })
    );
    push(&r.fake.exchange, Err(status(429, "request_locked")));
    let view = r.account.submit_code("000001").unwrap();
    assert!(matches!(
        view.phase,
        Phase::Ended {
            reason: EndReason::Locked,
            ..
        }
    ));
    assert!(r.store.names().is_empty());
    // A new code goes out for the same address and Mac.
    push(&r.fake.start, started("req-2"));
    let view = r.account.resend().unwrap();
    assert!(matches!(view.phase, Phase::CodeSent { .. }));
    assert_eq!(
        r.fake.starts.lock().unwrap()[1].device_name,
        "MacBook di Giulia"
    );
}

#[test]
fn an_expired_code_ends_the_request() {
    let r = rig();
    start(&r);
    push(&r.fake.exchange, Err(status(410, "request_expired")));
    let view = r.account.submit_code("123456").unwrap();
    assert!(matches!(
        view.phase,
        Phase::Ended {
            reason: EndReason::Expired,
            ..
        }
    ));
}

#[test]
fn a_rate_limit_says_until_when() {
    let r = rig();
    push(
        &r.fake.start,
        Err(ApiError::Status {
            status: 429,
            code: "rate_limited".into(),
            message: "Too many".into(),
            attempts_left: None,
            retry_after: Some(30),
            reason: None,
        }),
    );
    let view = r.account.start("giulia@studio.test", "Mac", false).unwrap();
    assert!(matches!(view.phase, Phase::SignedOut { .. }));
    let Some(AuthError::RateLimited { until_ms }) = view.error else {
        panic!("{view:?}");
    };
    let wait = until_ms.saturating_sub(now_ms());
    assert!((28_000..=30_000).contains(&wait), "{wait}");
}

#[test]
fn an_unreachable_server_keeps_the_form_and_says_so() {
    let r = rig();
    push(
        &r.fake.start,
        Err(ApiError::Network("connection refused".into())),
    );
    let view = r.account.start("giulia@studio.test", "Mac", false).unwrap();
    assert!(matches!(view.phase, Phase::SignedOut { .. }));
    assert!(matches!(view.error, Some(AuthError::Unreachable { .. })));
    assert_eq!(view.busy, None);
}

#[test]
fn bad_input_never_reaches_the_server() {
    let r = rig();
    let view = r.account.start("giulia", "Mac", false).unwrap();
    assert_eq!(view.error, Some(AuthError::InvalidEmail));
    let view = r.account.start("g@x.it", "   ", false).unwrap();
    assert_eq!(view.error, Some(AuthError::InvalidDeviceName));
    assert!(r.fake.starts.lock().unwrap().is_empty());
    assert_eq!(r.account.submit_code("123456"), Err(Refused::State));
}

#[test]
fn the_link_on_the_loopback_signs_in() {
    let r = rig();
    start(&r);
    push(&r.fake.exchange, granted());
    push(&r.fake.register, answer());
    let uri = r.fake.starts.lock().unwrap()[0].redirect_uri.clone();
    let port: u16 = uri[17..].split('/').next().unwrap().parse().unwrap();
    let query = uri.split_once('?').unwrap().1;
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    write!(s, "GET /callback?{query}&code=auth-1 HTTP/1.1\r\n\r\n").unwrap();
    let mut page = String::new();
    s.read_to_string(&mut page).unwrap();
    assert!(page.starts_with("HTTP/1.1 200"));

    let deadline = Instant::now() + Duration::from_secs(5);
    while !matches!(r.account.view().phase, Phase::SignedIn { .. }) {
        assert!(Instant::now() < deadline, "{:?}", r.account.view());
        thread::sleep(Duration::from_millis(20));
    }
    let (_, proof) = r.fake.exchanges.lock().unwrap()[0].clone();
    assert_eq!(proof, Proof::Link("auth-1".into()));
    assert!(
        r.events
            .lock()
            .unwrap()
            .iter()
            .any(|e| matches!(e, AccountEvent::LinkOpened))
    );
}

#[test]
fn a_link_opened_on_another_device_ends_the_wait() {
    let r = rig();
    start(&r);
    push(&r.fake.status, Ok("pending".into()));
    push(&r.fake.status, Ok("completed".into()));
    push(&r.fake.status, Ok("completed".into()));
    r.account.tick();
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::CodeSent { .. }));
    thread::sleep(Duration::from_millis(60));
    r.account.tick();
    assert!(matches!(
        r.account.view().phase,
        Phase::Ended {
            reason: EndReason::Elsewhere,
            ..
        }
    ));
}

#[test]
fn the_poll_ends_an_expired_or_locked_request() {
    for (answer, reason) in [
        ("expired", EndReason::Expired),
        ("locked", EndReason::Locked),
    ] {
        let r = rig();
        start(&r);
        push(&r.fake.status, Ok(answer.into()));
        r.account.tick();
        assert!(
            matches!(r.account.view().phase, Phase::Ended { reason: got, .. } if got == reason)
        );
    }
}

#[test]
fn the_same_request_back_keeps_its_verifier() {
    let r = rig();
    start(&r);
    r.account.back().unwrap();
    // Typed again within a minute: the server answers with the first request.
    start(&r);
    push(&r.fake.start, started("req-1"));
    let view = r.account.resend().unwrap();
    assert_eq!(view.error, Some(AuthError::NotResent));

    push(&r.fake.exchange, granted());
    push(&r.fake.register, answer());
    r.account.submit_code("123456").unwrap();
    let first = r.fake.starts.lock().unwrap()[0].code_challenge.clone();
    let (verifier, _) = r.fake.exchanges.lock().unwrap()[0].clone();
    assert_eq!(pkce::challenge(&verifier), first);
}

#[test]
fn registration_waits_for_valkey_and_retries() {
    let r = rig();
    start(&r);
    push(&r.fake.exchange, granted());
    push(&r.fake.register, Err(status(503, "metadata_unavailable")));
    let view = r.account.submit_code("123456").unwrap();
    assert!(matches!(view.phase, Phase::Registering { .. }));
    assert_eq!(view.error, Some(AuthError::MetadataUnavailable));
    assert_eq!(r.store.names(), vec!["device"]);

    push(&r.fake.register, answer());
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::SignedIn { .. }));
    assert_eq!(r.account.view().error, None);
}

#[test]
fn a_refused_keychain_after_the_exchange_signs_out_and_says_why() {
    let r = rig();
    start(&r);
    push(&r.fake.exchange, granted());
    r.store.failing.store(true, Ordering::SeqCst);
    let view = r.account.submit_code("123456").unwrap();
    assert!(matches!(view.phase, Phase::SignedOut { notice: None }));
    assert!(matches!(view.error, Some(AuthError::Keychain { .. })));
    assert!(r.fake.tokens.lock().unwrap().is_empty());
}

#[test]
fn a_launch_restores_the_account_from_the_keychain() {
    let r = rig_with(signed_in_store(false));
    assert!(matches!(r.account.view().phase, Phase::Registering { .. }));
    assert_eq!(r.account.view().server, "https://floppy.studio.test");
    push(&r.fake.register, answer());
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::SignedIn { .. }));
}

#[test]
fn revocation_by_an_admin_signs_out_and_clears_the_keychain() {
    let r = rig_with(signed_in_store(true));
    assert!(matches!(r.account.view().phase, Phase::SignedIn { .. }));
    push(&r.fake.heartbeat, beat("revoked", &[]));
    r.account.tick();
    let view = r.account.view();
    assert_eq!(
        view.phase,
        Phase::SignedOut {
            notice: Some(OutNotice::Revoked)
        }
    );
    assert!(r.store.names().is_empty());
    assert_eq!(view.server, server::DEFAULT_SERVER);
}

#[test]
fn a_token_the_server_no_longer_knows_counts_as_revoked() {
    let r = rig_with(signed_in_store(true));
    push(&r.fake.heartbeat, Err(status(401, "unauthorized")));
    r.account.tick();
    assert_eq!(
        r.account.view().phase,
        Phase::SignedOut {
            notice: Some(OutNotice::Revoked)
        }
    );
    assert!(r.store.names().is_empty());
}

#[test]
fn a_heartbeat_with_no_answer_keeps_the_access() {
    let r = rig_with(signed_in_store(true));
    push(&r.fake.heartbeat, Err(ApiError::Network("timeout".into())));
    r.account.tick();
    let view = r.account.view();
    assert!(matches!(view.phase, Phase::SignedIn { .. }));
    assert!(!view.reachable);
    assert_eq!(r.store.names().len(), 2);
    push(&r.fake.heartbeat, beat("active", &[("0192-a", "read")]));
    r.account.tick();
    assert!(r.account.view().reachable);
}

#[test]
fn sign_out_revokes_on_the_server_then_clears_the_keychain() {
    let r = rig_with(signed_in_store(true));
    push(&r.fake.sign_out, Ok(()));
    let view = r.account.sign_out(false).unwrap();
    assert_eq!(
        view.phase,
        Phase::SignedOut {
            notice: Some(OutNotice::SignedOut)
        }
    );
    assert!(r.store.names().is_empty());
    assert_eq!(r.fake.tokens.lock().unwrap()[0], "tok-secret");
}

#[test]
fn sign_out_without_the_server_keeps_the_access_until_asked_locally() {
    let r = rig_with(signed_in_store(true));
    push(&r.fake.sign_out, Err(ApiError::Network("refused".into())));
    let view = r.account.sign_out(false).unwrap();
    assert!(matches!(view.phase, Phase::SignedIn { .. }));
    assert!(matches!(view.error, Some(AuthError::SignOutFailed { .. })));
    assert_eq!(r.store.names().len(), 2);

    let view = r.account.sign_out(true).unwrap();
    assert!(matches!(view.phase, Phase::SignedOut { .. }));
    assert!(r.store.names().is_empty());
    assert_eq!(r.fake.tokens.lock().unwrap().len(), 1);
}

#[test]
fn sign_out_of_an_already_revoked_mac_still_clears() {
    let r = rig_with(signed_in_store(true));
    push(&r.fake.sign_out, Err(status(409, "invalid_state")));
    assert!(matches!(
        r.account.sign_out(false).unwrap().phase,
        Phase::SignedOut { .. }
    ));
    assert!(r.store.names().is_empty());
}

#[test]
fn the_server_setting_is_checked_kept_and_locked_while_signed_in() {
    let r = rig();
    let view = r
        .account
        .set_server(Some("http://floppy.studio.test"))
        .unwrap();
    assert_eq!(
        view.error,
        Some(AuthError::InvalidServer { reason: "insecure" })
    );
    let view = r
        .account
        .set_server(Some("floppy.studio.test/api/v1"))
        .unwrap();
    assert_eq!(view.server, "https://floppy.studio.test");
    assert_eq!(view.error, None);
    assert_eq!(
        r.account.prefs.load().server_url.as_deref(),
        Some("https://floppy.studio.test")
    );
    let view = r.account.set_server(None).unwrap();
    assert_eq!(view.server, server::DEFAULT_SERVER);
    assert_eq!(r.account.prefs.load().server_url, None);

    let signed = rig_with(signed_in_store(true));
    assert_eq!(signed.account.set_server(None), Err(Refused::State));
}

// The team disks: what the account hands the engine side and when.

fn signed_in_rig() -> Rig {
    rig_with(signed_in_store(true))
}

#[test]
fn registration_hands_the_disks_and_names_the_team() {
    let r = rig();
    start(&r);
    push(&r.fake.exchange, granted());
    push(&r.fake.register, answer());
    let view = r.account.submit_code("123456").unwrap();
    assert_eq!(
        view.phase,
        Phase::SignedIn {
            email: "giulia@studio.test".into(),
            device_name: "MacBook di Giulia".into(),
            organization: Some("Studio Esempio".into()),
            user_name: Some("Giulia Ferri".into()),
        }
    );
    {
        let configured = r.team.configured.lock().unwrap();
        assert_eq!(configured.len(), 1);
        assert_eq!(configured[0].0.disks[0].name, "Archivio 2025");
        assert_eq!(configured[0].1.valkey_password, "pw-secret");
    }
    // The names survive a relaunch, before any network.
    let again = rig_with(Arc::clone(&r.store));
    assert!(matches!(
        again.account.view().phase,
        Phase::SignedIn { organization: Some(ref o), .. } if o == "Studio Esempio"
    ));
}

#[test]
fn a_launch_reads_the_configuration_and_hands_it_with_the_keychain_secrets() {
    let r = signed_in_rig();
    push(
        &r.fake.config,
        Ok(config_with(&[("0192-a", api::Access::Write)])),
    );
    push(&r.fake.heartbeat, beat("active", &[("0192-a", "write")]));
    r.account.tick();
    let configured = r.team.configured.lock().unwrap().clone();
    assert_eq!(configured.len(), 1);
    assert_eq!(configured[0].0.disks[0].access, api::Access::Write);
    assert_eq!(configured[0].1.valkey_username, "floppy_d_1");
    // The same access in the next beat: no new read.
    push(&r.fake.heartbeat, beat("active", &[("0192-a", "write")]));
    r.account.tick();
    assert_eq!(r.team.configured.lock().unwrap().len(), 1);
    assert_eq!(r.fake.beats.lock().unwrap().len(), 2);
}

#[test]
fn an_access_change_in_the_heartbeat_reads_the_configuration_again() {
    let r = signed_in_rig();
    push(
        &r.fake.config,
        Ok(config_with(&[("0192-a", api::Access::Write)])),
    );
    *r.team.mounts.lock().unwrap() = MountReport {
        mounted_disk_ids: vec!["0192-a".into()],
        replica_mode: Some("replica".into()),
    };
    push(&r.fake.heartbeat, beat("active", &[("0192-a", "read")]));
    r.account.tick();
    let sent = r.fake.beats.lock().unwrap()[0].clone();
    assert_eq!(sent.mounted_disk_ids, ["0192-a"]);
    assert_eq!(sent.replica.unwrap().mode, "replica");
    assert_eq!(sent.s3_access_key_tail, None, "manual: no key of its own");
    // Lowered to read: the next tick reads and hands the new answer.
    push(
        &r.fake.config,
        Ok(config_with(&[("0192-a", api::Access::Read)])),
    );
    push(&r.fake.heartbeat, beat("active", &[("0192-a", "read")]));
    r.account.tick();
    let configured = r.team.configured.lock().unwrap();
    assert_eq!(configured.len(), 2);
    assert_eq!(configured[1].0.disks[0].access, api::Access::Read);
}

#[test]
fn a_managed_key_the_server_does_not_have_registers_again_once() {
    let r = signed_in_rig();
    let mut stale = config_with(&[("0192-a", api::Access::Write)]);
    stale.storage_mode = "managed".into();
    stale.s3_access_key_tail = Some("ZZZZZ".into());
    push(&r.fake.config, Ok(stale.clone()));
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::Registering { .. }));
    push(&r.fake.register, answer());
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::SignedIn { .. }));
    // Still another key: no loop, the answer is handed as it is.
    r.account.lock().config_due = true;
    push(&r.fake.config, Ok(stale));
    push(&r.fake.heartbeat, beat("active", &[("0192-a", "write")]));
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::SignedIn { .. }));
    assert_eq!(r.team.configured.lock().unwrap().len(), 2);
}

#[test]
fn revocation_clears_the_disks_before_the_keychain() {
    let r = signed_in_rig();
    push(&r.fake.heartbeat, beat("revoked", &[]));
    r.account.tick();
    assert_eq!(
        *r.team.clears.lock().unwrap(),
        [vec!["device".to_string(), "disk-credentials".to_string()]]
    );
    assert!(r.store.names().is_empty());
    assert_eq!(r.account.view().error, None);
    assert!(!r.account.prefs.load().disks_clear_owed);
}

#[test]
fn a_clear_that_fails_is_owed_and_retried() {
    let r = signed_in_rig();
    r.team.clear_fails.store(true, Ordering::SeqCst);
    push(&r.fake.heartbeat, beat("revoked", &[]));
    r.account.tick();
    let view = r.account.view();
    assert_eq!(
        view.phase,
        Phase::SignedOut {
            notice: Some(OutNotice::Revoked)
        }
    );
    assert!(matches!(
        view.error,
        Some(AuthError::DisksNotCleared { .. })
    ));
    assert!(
        r.store.names().is_empty(),
        "the access is gone all the same"
    );
    assert!(r.account.prefs.load().disks_clear_owed);
    r.account.tick();
    assert_eq!(r.team.clears.lock().unwrap().len(), 2);
    r.team.clear_fails.store(false, Ordering::SeqCst);
    r.account.tick();
    assert_eq!(r.account.view().error, None);
    assert!(!r.account.prefs.load().disks_clear_owed);
    r.account.tick();
    assert_eq!(r.team.clears.lock().unwrap().len(), 3, "done: no more");
}

#[test]
fn an_owed_clear_from_a_previous_launch_is_retried() {
    let dir = tempfile::tempdir().unwrap();
    let prefs = PrefsStore::new(dir.path());
    prefs.update(|p| p.disks_clear_owed = true).unwrap();
    let team = Arc::new(FakeTeam::default());
    let account = Account::new(
        Arc::new(Fake::default()) as Arc<dyn ControlPlane>,
        Arc::new(MemoryStore::default()) as Arc<dyn SecretStore>,
        Arc::clone(&team) as Arc<dyn TeamDisks>,
        prefs.clone(),
        Versions {
            app_version: "0.1.0".into(),
            macos_version: "15.5".into(),
        },
        quick(),
        |_| {},
    );
    account.tick();
    assert_eq!(team.clears.lock().unwrap().len(), 1);
    assert!(!prefs.load().disks_clear_owed);
}

#[test]
fn each_refusal_reason_reads_as_its_own_notice() {
    for (reason, notice) in [
        ("token_unknown", OutNotice::Unknown),
        ("device_revoked", OutNotice::Revoked),
        ("device_signed_out", OutNotice::AlreadySignedOut),
    ] {
        let r = signed_in_rig();
        push(&r.fake.config, Err(refused_for(reason)));
        r.account.tick();
        assert_eq!(
            r.account.view().phase,
            Phase::SignedOut {
                notice: Some(notice)
            },
            "{reason}"
        );
        assert_eq!(r.team.clears.lock().unwrap().len(), 1, "{reason}");
        assert!(r.store.names().is_empty());
    }
}

#[test]
fn a_mac_the_server_holds_as_pending_registers_again() {
    let r = signed_in_rig();
    push(
        &r.fake.config,
        Ok(config_with(&[("0192-a", api::Access::Read)])),
    );
    push(&r.fake.heartbeat, Err(refused_for("device_not_registered")));
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::Registering { .. }));
    assert!(
        r.team.clears.lock().unwrap().is_empty(),
        "nothing is cleared"
    );
    assert_eq!(r.store.names().len(), 2);
    push(&r.fake.register, answer());
    r.account.tick();
    assert!(matches!(r.account.view().phase, Phase::SignedIn { .. }));
}

#[test]
fn sign_out_clears_the_disks_too_even_locally() {
    let r = signed_in_rig();
    push(&r.fake.sign_out, Ok(()));
    r.account.sign_out(false).unwrap();
    assert_eq!(
        r.team.clears.lock().unwrap()[0].len(),
        2,
        "before the Keychain"
    );
    assert!(r.store.names().is_empty());

    let r = signed_in_rig();
    push(&r.fake.sign_out, Err(ApiError::Network("refused".into())));
    r.account.sign_out(false).unwrap();
    assert!(r.team.clears.lock().unwrap().is_empty(), "still signed in");
    r.account.sign_out(true).unwrap();
    assert_eq!(r.team.clears.lock().unwrap().len(), 1);
}
