//! Signing this Mac in to a control plane and keeping it registered
//! (docs/architecture.html, "First install and login"; DATABASE_SCHEMA.md,
//! "Mac enrolment and revocation"):
//!
//! 1. `start`: an email and a PKCE challenge go to `/auth/app/sign-in`, with a
//!    loopback address for the link; the mail carries a link and a 6-digit code.
//! 2. The code typed here, or the link opened on this Mac (it ends on the
//!    loopback), is traded with the verifier for a device token: the Mac is
//!    `pending` on the server, and the token goes to the Keychain at once.
//! 3. `/device/register` makes it `active` and returns its disk credentials
//!    once (`credentials.rs`) with the disks it may see, which go to the
//!    engine side (`TeamDisks`, implemented by `core`).
//! 4. A heartbeat every minute reports the mounts and answers what the Mac
//!    may see now: when a disk or its access changed, `/device/config` is
//!    read again and handed to the engine side.
//! 5. Revoked (the heartbeat, or a 401 with its `details.reason`), or signed
//!    out by the person: every team disk is cleared off the Mac first
//!    (`floppy disks clear`), then the Keychain. A clear that fails is owed
//!    (kept in the preferences) and retried every minute.
//!
//! Sign-out clears the disks too. The server revokes the Mac's Valkey users
//! (and, managed, its S3 key) at sign-out without opening a key window,
//! because the app wipes the Mac: a signed-out Mac keeping the team's cache
//! and replicas would break that. A signed-out Mac has no team disks.
//!
//! Network calls never run under the lock; one step runs at a time (`busy`),
//! and the worker thread polls the waiting request, retries registration and
//! sends the heartbeat. Events go out through a callback: no Tauri here.

pub mod api;
pub mod credentials;
pub mod loopback;
pub mod pkce;
pub mod secrets;
pub mod server;

use crate::prefs::PrefsStore;
use api::{
    ApiError, Beat, ControlPlane, DeviceConfig, DeviceRefusal, HeartbeatRequest, Proof,
    ReplicaReport, StartRequest, Versions,
};
use credentials::DiskCredentials;
use loopback::Loopback;
use secrets::SecretStore;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, Weak};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// The Keychain item with the device token and who it belongs to.
pub const DEVICE_ITEM: &str = "device";
/// The longest device name the server keeps.
pub const DEVICE_NAME_MAX: usize = 100;

/// How often the steps that wait on the server run.
#[derive(Debug, Clone, Copy)]
pub struct Timing {
    /// The worker's tick.
    pub tick: Duration,
    /// Polling a waiting request (the link may be opened on another device).
    pub poll: Duration,
    /// A request completed with no link on the loopback after this was opened elsewhere.
    pub elsewhere_after: Duration,
    /// A request lives 15 minutes on the server; the loopback as long.
    pub request_ttl: Duration,
    pub register_retry: Duration,
    pub heartbeat: Duration,
    /// Reading `/device/config` again after a failure.
    pub config_retry: Duration,
    /// Clearing the team disks again after a failure.
    pub clear_retry: Duration,
}

impl Default for Timing {
    fn default() -> Self {
        Timing {
            tick: Duration::from_secs(1),
            poll: Duration::from_secs(3),
            elsewhere_after: Duration::from_secs(5),
            request_ttl: Duration::from_secs(15 * 60),
            register_retry: Duration::from_secs(30),
            heartbeat: Duration::from_secs(60),
            config_retry: Duration::from_secs(30),
            clear_retry: Duration::from_secs(60),
        }
    }
}

/// What the account asks of the engine side: `core::Core` in the app, a
/// recording fake in the tests.
pub trait TeamDisks: Send + Sync {
    /// The disks the control plane lets this Mac see, with what mounts them.
    fn configure(&self, config: &DeviceConfig, credentials: &DiskCredentials);
    /// What the heartbeat reports about the mounts.
    fn report(&self) -> MountReport;
    /// Every team disk off this Mac (`floppy disks clear`); the engine's
    /// message when it could not.
    fn clear(&self) -> Result<(), String>;
}

/// The mounts as the heartbeat reports them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MountReport {
    pub mounted_disk_ids: Vec<String>,
    /// `replica`, `master` or `offline`: the worst among the mounted disks.
    pub replica_mode: Option<String>,
    /// The largest lag among the mounted disks, an upper bound.
    pub replica_lag_ms: Option<u64>,
}

/// The Keychain record of a signed-in Mac.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
struct DeviceRecord {
    server: String,
    device_id: String,
    device_token: String,
    email: String,
    device_name: String,
    registered: bool,
    /// The team and the person's name, from the last answer.
    #[serde(default)]
    organization: Option<String>,
    #[serde(default)]
    user_name: Option<String>,
}

impl std::fmt::Debug for DeviceRecord {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DeviceRecord")
            .field("server", &self.server)
            .field("device_id", &self.device_id)
            .field("registered", &self.registered)
            .finish_non_exhaustive()
    }
}

/// Why the app is signed out, when it was not the person's choice alone.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OutNotice {
    /// An admin (or the person from the web) revoked this Mac.
    Revoked,
    /// The person signed out from this app.
    SignedOut,
    /// The server does not know this Mac (`token_unknown`): deleted, or
    /// another server answers at the address.
    Unknown,
    /// The server says this Mac had already signed out (`device_signed_out`).
    AlreadySignedOut,
}

/// Why a waiting request can no longer complete.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EndReason {
    Expired,
    /// Five wrong codes.
    Locked,
    /// The link was opened on another device: its browser got the code.
    Elsewhere,
}

/// Where the sign-in is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum Phase {
    SignedOut {
        notice: Option<OutNotice>,
    },
    /// Waiting for the code or the link; `resend_at` is when a new mail may go.
    CodeSent {
        email: String,
        device_name: String,
        expires_at: String,
        resend_at: String,
    },
    Ended {
        reason: EndReason,
        email: String,
        device_name: String,
    },
    /// Signed in, the Mac not registered yet (or registration to retry).
    Registering {
        email: String,
        device_name: String,
    },
    SignedIn {
        email: String,
        device_name: String,
        /// The team's name, once the server has told it.
        organization: Option<String>,
        /// The person's name (the address when they set none).
        user_name: Option<String>,
    },
}

/// The step running now.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Busy {
    Sending,
    Checking,
    Linking,
    Registering,
    SigningOut,
    /// Removing the team disks of a Mac that lost its access.
    Clearing,
}

/// What went wrong in the last step, for the dictionary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "code", rename_all = "snake_case")]
pub enum AuthError {
    InvalidEmail,
    InvalidDeviceName,
    InvalidServer {
        reason: &'static str,
    },
    Unreachable {
        detail: String,
    },
    RateLimited {
        until_ms: u64,
    },
    InvalidCode {
        attempts_left: Option<u32>,
    },
    /// A resend the server did not send: too many mails this hour.
    NotResent,
    MailFailed,
    MetadataUnavailable,
    SignOutFailed {
        detail: String,
    },
    /// Signed out, but the team disks are still on the Mac: retried.
    DisksNotCleared {
        detail: String,
    },
    Keychain {
        detail: String,
    },
    Settings {
        detail: String,
    },
    Server {
        detail: String,
    },
}

/// Everything the windows show about the account.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AuthView {
    #[serde(flatten)]
    pub phase: Phase,
    /// The server in use: the token's when signed in, else the configured one.
    pub server: String,
    pub server_display: String,
    pub default_server: String,
    pub busy: Option<Busy>,
    pub error: Option<AuthError>,
    /// The last heartbeat got an answer.
    pub reachable: bool,
}

/// What the account tells the app shell.
#[derive(Debug, Clone)]
pub enum AccountEvent {
    Changed(Box<AuthView>),
    /// The link landed on the loopback: bring the sign-in window forward.
    LinkOpened,
}

/// A command refused before any call.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum Refused {
    #[error("another sign-in step is running")]
    Busy,
    #[error("not possible in the current sign-in state")]
    State,
}

struct Pending {
    email: String,
    device_name: String,
    server: String,
    verifier: String,
    state: String,
    request_id: String,
    /// Held to keep the listener up while the request waits; dropping stops it.
    _loopback: Loopback,
    last_poll: Instant,
    completed_seen: Option<Instant>,
}

struct Inner {
    configured_server: String,
    phase: Phase,
    pending: Option<Pending>,
    record: Option<DeviceRecord>,
    busy: Option<Busy>,
    error: Option<AuthError>,
    reachable: bool,
    last_register: Option<Instant>,
    last_beat: Option<Instant>,
    /// In memory from the Keychain; None until registered.
    credentials: Option<DiskCredentials>,
    /// The last `/device/config` (or registration) answer.
    config: Option<DeviceConfig>,
    config_due: bool,
    last_config: Option<Instant>,
    /// Registered again this run because the S3 key was not the server's.
    reissued: bool,
    /// The team disks are still to be cleared (also in the preferences).
    clear_owed: bool,
    last_clear: Option<Instant>,
}

type Emit = Box<dyn Fn(AccountEvent) + Send + Sync>;

/// The account service, shared by the commands, the worker and the loopback.
pub struct Account {
    inner: Mutex<Inner>,
    api: Arc<dyn ControlPlane>,
    secrets: Arc<dyn SecretStore>,
    team: Arc<dyn TeamDisks>,
    prefs: PrefsStore,
    versions: Versions,
    timing: Timing,
    emit: Emit,
    worker: AtomicBool,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// The same shape check the server makes: one `@`, a dot in the domain.
pub fn valid_email(email: &str) -> bool {
    if email.len() > 254 || email.chars().any(char::is_whitespace) {
        return false;
    }
    let Some((local, domain)) = email.split_once('@') else {
        return false;
    };
    if local.is_empty() || domain.contains('@') {
        return false;
    }
    matches!(domain.rsplit_once('.'), Some((a, tld)) if !a.is_empty() && !tld.is_empty())
}

/// The six digits of a code as typed or pasted (spaces, dashes dropped).
pub fn clean_code(code: &str) -> Option<String> {
    let digits: String = code.chars().filter(char::is_ascii_digit).collect();
    (digits.len() == 6).then_some(digits)
}

fn unreachable(e: &ApiError) -> AuthError {
    AuthError::Unreachable {
        detail: e.to_string(),
    }
}

fn server_error(e: &ApiError) -> AuthError {
    match e {
        ApiError::Network(_) => unreachable(e),
        ApiError::Status {
            code, retry_after, ..
        } if code == "rate_limited" => AuthError::RateLimited {
            until_ms: now_ms() + retry_after.unwrap_or(60) * 1000,
        },
        _ => AuthError::Server {
            detail: e.to_string(),
        },
    }
}

impl Account {
    /// The account, restored from the Keychain: signed in, registering, or out.
    pub fn new(
        api: Arc<dyn ControlPlane>,
        secrets: Arc<dyn SecretStore>,
        team: Arc<dyn TeamDisks>,
        prefs: PrefsStore,
        versions: Versions,
        timing: Timing,
        emit: impl Fn(AccountEvent) + Send + Sync + 'static,
    ) -> Arc<Self> {
        let stored = prefs.load();
        let clear_owed = stored.disks_clear_owed;
        let configured_server = stored
            .server_url
            .and_then(|s| server::normalize(&s).ok())
            .unwrap_or_else(|| server::DEFAULT_SERVER.to_string());
        let mut error = None;
        let record = match secrets.get(DEVICE_ITEM) {
            Ok(Some(bytes)) => match serde_json::from_slice::<DeviceRecord>(&bytes) {
                Ok(r) => Some(r),
                Err(e) => {
                    error = Some(AuthError::Keychain {
                        detail: format!("unreadable sign-in item: {}", api::shape_error(&e)),
                    });
                    None
                }
            },
            Ok(None) => None,
            Err(e) => {
                error = Some(AuthError::Keychain {
                    detail: e.to_string(),
                });
                None
            }
        };
        // A Keychain that refuses this item is not fatal: the Mac registers
        // again and gets new ones.
        let credentials = match &record {
            Some(r) if r.registered => credentials::load(secrets.as_ref()).ok().flatten(),
            _ => None,
        };
        let registered = record.as_ref().is_some_and(|r| r.registered);
        let phase = match &record {
            Some(r) if r.registered => signed_in(r),
            Some(r) => Phase::Registering {
                email: r.email.clone(),
                device_name: r.device_name.clone(),
            },
            None => Phase::SignedOut { notice: None },
        };
        Arc::new(Account {
            inner: Mutex::new(Inner {
                configured_server,
                phase,
                pending: None,
                record,
                busy: None,
                error,
                reachable: true,
                last_register: None,
                last_beat: None,
                config_due: registered,
                credentials,
                config: None,
                last_config: None,
                reissued: false,
                clear_owed,
                last_clear: None,
            }),
            api,
            secrets,
            team,
            prefs,
            versions,
            timing,
            emit: Box::new(emit),
            worker: AtomicBool::new(false),
        })
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn compose(inner: &Inner) -> AuthView {
        let server = inner
            .record
            .as_ref()
            .map(|r| r.server.clone())
            .or_else(|| inner.pending.as_ref().map(|p| p.server.clone()))
            .unwrap_or_else(|| inner.configured_server.clone());
        AuthView {
            phase: inner.phase.clone(),
            server_display: server::display(&server),
            server,
            default_server: server::DEFAULT_SERVER.to_string(),
            busy: inner.busy,
            error: inner.error.clone(),
            reachable: inner.reachable,
        }
    }

    /// The server and device token while this Mac is signed in and
    /// registered, for the activity's calls.
    pub fn device(&self) -> Option<crate::activity::DeviceAuth> {
        let inner = self.lock();
        match (&inner.phase, &inner.record) {
            (Phase::SignedIn { .. }, Some(r)) if r.registered => {
                Some(crate::activity::DeviceAuth {
                    server: r.server.clone(),
                    token: r.device_token.clone(),
                })
            }
            _ => None,
        }
    }

    /// What the windows show now.
    pub fn view(&self) -> AuthView {
        Self::compose(&self.lock())
    }

    fn publish(&self, inner: &Inner) {
        (self.emit)(AccountEvent::Changed(Box::new(Self::compose(inner))));
    }

    /// Applies a change and tells the windows.
    fn update<T>(&self, f: impl FnOnce(&mut Inner) -> T) -> T {
        let mut inner = self.lock();
        let out = f(&mut inner);
        self.publish(&inner);
        out
    }

    /// Marks a step as running, if none is.
    fn begin(&self, busy: Busy, allowed: impl FnOnce(&Inner) -> bool) -> Result<(), Refused> {
        let mut inner = self.lock();
        if inner.busy.is_some() {
            return Err(Refused::Busy);
        }
        if !allowed(&inner) {
            return Err(Refused::State);
        }
        inner.busy = Some(busy);
        inner.error = None;
        self.publish(&inner);
        Ok(())
    }

    // Sign-in.

    /// Asks for a link and a code by mail. `resend` is the "send another"
    /// button: a resend the server did not make is an error; the same address
    /// typed again while its request waits simply keeps waiting on it.
    pub fn start(
        self: &Arc<Self>,
        email: &str,
        device_name: &str,
        resend: bool,
    ) -> Result<AuthView, Refused> {
        let email = email.trim().to_lowercase();
        let device_name: String = device_name.trim().chars().take(DEVICE_NAME_MAX).collect();
        let local_error = if !valid_email(&email) {
            Some(AuthError::InvalidEmail)
        } else if device_name.is_empty() {
            Some(AuthError::InvalidDeviceName)
        } else {
            None
        };
        let can_start = |i: &Inner| {
            i.record.is_none()
                && matches!(
                    i.phase,
                    Phase::SignedOut { .. } | Phase::Ended { .. } | Phase::CodeSent { .. }
                )
        };
        if let Some(error) = local_error {
            let mut inner = self.lock();
            if inner.busy.is_some() {
                return Err(Refused::Busy);
            }
            if !can_start(&inner) {
                return Err(Refused::State);
            }
            inner.error = Some(error);
            self.publish(&inner);
            return Ok(Self::compose(&inner));
        }
        self.begin(Busy::Sending, can_start)?;

        let server = self.lock().configured_server.clone();
        let prepared = pkce::verifier().and_then(|v| Ok((v, pkce::random_token(16)?)));
        let (verifier, state) = match prepared {
            Ok(pair) => pair,
            Err(e) => {
                return Ok(self.fail(AuthError::Server {
                    detail: format!("no randomness: {e}"),
                }));
            }
        };
        let weak = Arc::downgrade(self);
        let cb_state = state.clone();
        let loopback = match Loopback::start(state.clone(), self.timing.request_ttl, move |code| {
            if let Some(account) = Weak::upgrade(&weak) {
                account.complete_link(&cb_state, code);
            }
        }) {
            Ok(l) => l,
            Err(e) => {
                return Ok(self.fail(AuthError::Server {
                    detail: format!("no loopback port: {e}"),
                }));
            }
        };
        let req = StartRequest {
            email: email.clone(),
            code_challenge: pkce::challenge(&verifier),
            redirect_uri: loopback.redirect_uri().to_string(),
            device_name: device_name.clone(),
        };
        let answer = self.api.start_sign_in(&server, &req);

        Ok(self.update(|inner| {
            inner.busy = None;
            match answer {
                Ok(resp) => {
                    let same = inner
                        .pending
                        .as_ref()
                        .is_some_and(|p| p.request_id == resp.request_id);
                    if same {
                        // The server kept the request it already had, bound to the
                        // earlier verifier and loopback: keep waiting on those.
                        if resend {
                            inner.error = Some(AuthError::NotResent);
                        }
                        if let Some(p) = inner.pending.as_ref() {
                            inner.phase = Phase::CodeSent {
                                email: p.email.clone(),
                                device_name: p.device_name.clone(),
                                expires_at: resp.expires_at,
                                resend_at: resp.resend_at,
                            };
                        }
                    } else {
                        inner.phase = Phase::CodeSent {
                            email: resp.email.clone(),
                            device_name: device_name.clone(),
                            expires_at: resp.expires_at,
                            resend_at: resp.resend_at.clone(),
                        };
                        inner.pending = Some(Pending {
                            email: resp.email,
                            device_name,
                            server,
                            verifier,
                            state,
                            request_id: resp.request_id,
                            _loopback: loopback,
                            last_poll: Instant::now(),
                            completed_seen: None,
                        });
                    }
                }
                Err(e) => {
                    inner.error = Some(match &e {
                        ApiError::Status { code, .. } if code == "validation" => {
                            AuthError::InvalidEmail
                        }
                        ApiError::Status { code, .. } if code == "mail_failed" => {
                            AuthError::MailFailed
                        }
                        _ => server_error(&e),
                    });
                }
            }
            Self::compose(inner)
        }))
    }

    /// Sends another mail for the waiting request (after its `resend_at`).
    pub fn resend(self: &Arc<Self>) -> Result<AuthView, Refused> {
        let (email, device_name) = {
            let inner = self.lock();
            match (&inner.phase, &inner.pending) {
                (Phase::CodeSent { .. }, Some(p)) => (p.email.clone(), p.device_name.clone()),
                (
                    Phase::Ended {
                        email, device_name, ..
                    },
                    _,
                ) => (email.clone(), device_name.clone()),
                _ => return Err(Refused::State),
            }
        };
        self.start(&email, &device_name, true)
    }

    fn fail(&self, error: AuthError) -> AuthView {
        self.update(|inner| {
            inner.busy = None;
            inner.error = Some(error);
            Self::compose(inner)
        })
    }

    /// Back to the email form. A waiting request stays usable: the same
    /// address typed again keeps waiting on it, and its link still works.
    pub fn back(&self) -> Result<AuthView, Refused> {
        let mut inner = self.lock();
        if inner.busy.is_some() {
            return Err(Refused::Busy);
        }
        if !matches!(inner.phase, Phase::CodeSent { .. } | Phase::Ended { .. }) {
            return Err(Refused::State);
        }
        inner.phase = Phase::SignedOut { notice: None };
        inner.error = None;
        self.publish(&inner);
        Ok(Self::compose(&inner))
    }

    /// Trades the 6-digit code for a device token, then registers.
    pub fn submit_code(self: &Arc<Self>, code: &str) -> Result<AuthView, Refused> {
        let Some(code) = clean_code(code) else {
            return self.update(|inner| {
                if !matches!(inner.phase, Phase::CodeSent { .. }) {
                    return Err(Refused::State);
                }
                inner.error = Some(AuthError::InvalidCode {
                    attempts_left: None,
                });
                Ok(Self::compose(inner))
            });
        };
        self.begin(Busy::Checking, |i| {
            matches!(i.phase, Phase::CodeSent { .. }) && i.pending.is_some()
        })?;
        self.exchange(Proof::Code(code));
        Ok(self.view())
    }

    /// The loopback got the link's one-time code for the request with `state`.
    fn complete_link(self: &Arc<Self>, state: &str, auth_code: String) {
        // The person may be typing the code at the same moment: let that step end.
        let deadline = Instant::now() + api::TIMEOUT + Duration::from_secs(1);
        loop {
            {
                let mut inner = self.lock();
                let ours = inner.pending.as_ref().is_some_and(|p| p.state == state)
                    && matches!(
                        inner.phase,
                        Phase::CodeSent { .. } | Phase::SignedOut { .. }
                    );
                if !ours {
                    return;
                }
                if inner.busy.is_none() {
                    inner.busy = Some(Busy::Linking);
                    inner.error = None;
                    self.publish(&inner);
                    break;
                }
            }
            if Instant::now() > deadline {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        (self.emit)(AccountEvent::LinkOpened);
        self.exchange(Proof::Link(auth_code));
    }

    /// The token exchange for the waiting request; `busy` is already set.
    fn exchange(self: &Arc<Self>, proof: Proof) {
        let Some((server, verifier)) = self
            .lock()
            .pending
            .as_ref()
            .map(|p| (p.server.clone(), p.verifier.clone()))
        else {
            self.update(|inner| inner.busy = None);
            return;
        };
        let answer = self.api.exchange(&server, &verifier, &proof);
        let grant = match answer {
            Ok(g) => g,
            Err(e) => {
                self.update(|inner| {
                    inner.busy = None;
                    let ended = Self::ended_for(inner.pending.as_ref());
                    match e.code() {
                        Some("invalid_code") => {
                            let attempts_left = match &e {
                                ApiError::Status { attempts_left, .. } => *attempts_left,
                                _ => None,
                            };
                            inner.error = Some(AuthError::InvalidCode { attempts_left });
                        }
                        Some("request_locked") => inner.phase = ended(EndReason::Locked),
                        Some("request_expired") | Some("link_invalid") => {
                            inner.phase = ended(EndReason::Expired)
                        }
                        _ => inner.error = Some(server_error(&e)),
                    }
                    if matches!(inner.phase, Phase::Ended { .. }) {
                        inner.pending = None;
                    }
                });
                return;
            }
        };
        let saved = self.update(|inner| {
            let Some(p) = inner.pending.take() else {
                inner.busy = None;
                return false;
            };
            let record = DeviceRecord {
                server: p.server,
                device_id: grant.device_id,
                device_token: grant.device_token,
                email: p.email,
                device_name: p.device_name,
                registered: false,
                organization: None,
                user_name: None,
            };
            match self.save_record(&record) {
                Ok(()) => {
                    inner.phase = Phase::Registering {
                        email: record.email.clone(),
                        device_name: record.device_name.clone(),
                    };
                    inner.record = Some(record);
                    inner.busy = Some(Busy::Registering);
                    true
                }
                Err(e) => {
                    // The token is lost: the server revokes a Mac that never
                    // registers after a day (`never_registered`).
                    inner.phase = Phase::SignedOut { notice: None };
                    inner.busy = None;
                    inner.error = Some(AuthError::Keychain {
                        detail: e.to_string(),
                    });
                    false
                }
            }
        });
        if saved {
            self.register_now();
        }
    }

    /// The `Ended` phase for the waiting request, by reason.
    fn ended_for(pending: Option<&Pending>) -> impl Fn(EndReason) -> Phase + use<> {
        let (email, device_name) = pending
            .map(|p| (p.email.clone(), p.device_name.clone()))
            .unwrap_or_default();
        move |reason| Phase::Ended {
            reason,
            email: email.clone(),
            device_name: device_name.clone(),
        }
    }

    fn save_record(&self, record: &DeviceRecord) -> Result<(), secrets::SecretError> {
        let json = serde_json::to_vec(record).map_err(|e| secrets::SecretError {
            message: api::shape_error(&e),
        })?;
        self.secrets.set(DEVICE_ITEM, &json)
    }

    // Registration and heartbeat.

    /// Retries registration now (the "Riprova" button).
    pub fn retry_register(self: &Arc<Self>) -> Result<AuthView, Refused> {
        self.begin(Busy::Registering, |i| {
            matches!(i.phase, Phase::Registering { .. }) && i.record.is_some()
        })?;
        self.register_now();
        Ok(self.view())
    }

    /// Registers the Mac; `busy` is already `Registering`.
    fn register_now(&self) {
        let Some(record) = self.lock().record.clone() else {
            self.update(|inner| inner.busy = None);
            return;
        };
        let answer = self
            .api
            .register(&record.server, &record.device_token, &self.versions);
        match answer {
            Ok(answer) => {
                let creds = DiskCredentials::from_register(&answer);
                let config = answer.config();
                let kept = credentials::keep(self.secrets.as_ref(), &creds);
                let done = self.update(|inner| {
                    inner.busy = None;
                    inner.last_register = Some(Instant::now());
                    if let Err(e) = kept {
                        inner.error = Some(AuthError::Keychain {
                            detail: e.to_string(),
                        });
                        return false;
                    }
                    let registered = DeviceRecord {
                        registered: true,
                        organization: Some(config.organization.name.clone()),
                        user_name: Some(config.user.name.clone()),
                        ..record
                    };
                    match self.save_record(&registered) {
                        Ok(()) => {
                            inner.phase = signed_in(&registered);
                            inner.record = Some(registered);
                            inner.reachable = true;
                            inner.error = None;
                            inner.last_beat = Some(Instant::now());
                            inner.credentials = Some(creds.clone());
                            inner.config = Some(config.clone());
                            // The register answer has no metadata CA: the
                            // configuration, read at the next tick, brings it.
                            inner.config_due = true;
                            inner.last_config = None;
                            // A new registration reconciles whatever the last left.
                            inner.clear_owed = false;
                            true
                        }
                        Err(e) => {
                            inner.error = Some(AuthError::Keychain {
                                detail: e.to_string(),
                            });
                            false
                        }
                    }
                });
                if done {
                    let _ = self.prefs.update(|p| p.disks_clear_owed = false);
                    self.team.configure(&config, &creds);
                }
            }
            Err(e) if e.is_unauthorized() => self.refused(&e, true),
            Err(e) => self.update(|inner| {
                inner.busy = None;
                inner.last_register = Some(Instant::now());
                inner.error = Some(match e.code() {
                    Some("metadata_unavailable") => AuthError::MetadataUnavailable,
                    _ => server_error(&e),
                });
            }),
        }
    }

    fn heartbeat(&self) {
        let (record, tail) = {
            let inner = self.lock();
            let Some(record) = inner.record.clone() else {
                return;
            };
            (
                record,
                inner
                    .credentials
                    .as_ref()
                    .and_then(DiskCredentials::s3_tail),
            )
        };
        let mounts = self.team.report();
        let beat = HeartbeatRequest {
            versions: self.versions.clone(),
            replica: mounts.replica_mode.map(|mode| ReplicaReport {
                mode,
                lag_ms: mounts.replica_lag_ms,
            }),
            s3_access_key_tail: tail,
            mounted_disk_ids: mounts.mounted_disk_ids,
        };
        let answer = self
            .api
            .heartbeat(&record.server, &record.device_token, &beat);
        // A step started meanwhile (sign-out) owns `busy`: the beat never touches it.
        match answer {
            Ok(a) if a.state == Beat::Revoked => self.lose_access(OutNotice::Revoked, false),
            Err(e) if e.is_unauthorized() => self.refused(&e, false),
            other => self.update(|inner| {
                inner.last_beat = Some(Instant::now());
                match other {
                    Ok(a) => {
                        inner.reachable = true;
                        // A disk given, taken away or with another access: read
                        // the configuration again and hand it to the engine.
                        let mut now: Vec<(String, api::Access)> =
                            a.disks.into_iter().map(|d| (d.id, d.access)).collect();
                        now.sort();
                        let mut known: Vec<(String, api::Access)> = inner
                            .config
                            .as_ref()
                            .map(|c| c.disks.iter().map(|d| (d.id.clone(), d.access)).collect())
                            .unwrap_or_default();
                        known.sort();
                        if inner.config.is_none() || now != known {
                            inner.config_due = true;
                            inner.last_config = None;
                        }
                    }
                    Err(ApiError::Network(_)) => inner.reachable = false,
                    Err(ApiError::Status { status, .. }) if status >= 500 => {
                        inner.reachable = false
                    }
                    Err(e) => {
                        inner.reachable = true;
                        inner.error = Some(server_error(&e));
                    }
                }
            }),
        }
    }

    /// Reads `/device/config` and hands it to the engine side.
    fn fetch_config(&self) {
        let Some(record) = self.lock().record.clone() else {
            return;
        };
        let answer = self.api.config(&record.server, &record.device_token);
        let config = match answer {
            Ok(c) => c,
            Err(e) if e.is_unauthorized() => return self.refused(&e, false),
            Err(e) => {
                return self.update(|inner| {
                    inner.last_config = Some(Instant::now());
                    inner.reachable = !matches!(
                        e,
                        ApiError::Network(_) | ApiError::Status { status: 500.., .. }
                    );
                });
            }
        };
        let creds = self.update(|inner| {
            inner.last_config = Some(Instant::now());
            inner.config_due = false;
            inner.reachable = true;
            // Managed: a key the server no longer has would fail every mount.
            // Registering again issues a new one (once per run, not in a loop).
            let tail = inner
                .credentials
                .as_ref()
                .and_then(DiskCredentials::s3_tail);
            let stale = config.storage_mode == "managed" && tail != config.s3_access_key_tail;
            if (stale || inner.credentials.is_none()) && !inner.reissued {
                inner.reissued = true;
                inner.phase = Phase::Registering {
                    email: record.email.clone(),
                    device_name: record.device_name.clone(),
                };
                inner.last_register = None;
                return None;
            }
            let named = DeviceRecord {
                organization: Some(config.organization.name.clone()),
                user_name: Some(config.user.name.clone()),
                ..record.clone()
            };
            if inner.record.as_ref() != Some(&named) && self.save_record(&named).is_ok() {
                inner.record = Some(named.clone());
            }
            if matches!(inner.phase, Phase::SignedIn { .. }) {
                inner.phase = signed_in(inner.record.as_ref().unwrap_or(&named));
            }
            inner.config = Some(config.clone());
            inner.credentials.clone()
        });
        if let Some(creds) = creds {
            self.team.configure(&config, &creds);
        }
    }

    /// Removes the token and the disk credentials from the Keychain.
    fn clear_keychain(&self) -> Result<(), secrets::SecretError> {
        credentials::clear(self.secrets.as_ref())?;
        self.secrets.delete(DEVICE_ITEM)
    }

    /// Clears the team disks, owed in the preferences until it succeeds, so
    /// a crash or a failure is retried (`tick`), also after a relaunch.
    fn clear_disks(&self) -> Result<(), String> {
        let _ = self.prefs.update(|p| p.disks_clear_owed = true);
        let result = self.team.clear();
        if result.is_ok() {
            let _ = self.prefs.update(|p| p.disks_clear_owed = false);
        }
        let mut inner = self.lock();
        inner.clear_owed = result.is_err();
        inner.last_clear = Some(Instant::now());
        result
    }

    /// A 401: back to registering for a Mac the server holds as pending,
    /// signed out with the reason otherwise.
    fn refused(&self, e: &ApiError, ending_step: bool) {
        let notice = match e.refusal() {
            Some(DeviceRefusal::NotRegistered) => {
                let record = self.lock().record.clone();
                let Some(record) = record else { return };
                let pending = DeviceRecord {
                    registered: false,
                    ..record
                };
                let saved = self.save_record(&pending);
                return self.update(|inner| {
                    if ending_step {
                        inner.busy = None;
                    }
                    inner.phase = Phase::Registering {
                        email: pending.email.clone(),
                        device_name: pending.device_name.clone(),
                    };
                    inner.record = Some(pending);
                    inner.last_register = None;
                    inner.error = saved.err().map(|e| AuthError::Keychain {
                        detail: e.to_string(),
                    });
                });
            }
            Some(DeviceRefusal::TokenUnknown) => OutNotice::Unknown,
            Some(DeviceRefusal::SignedOut) => OutNotice::AlreadySignedOut,
            Some(DeviceRefusal::Revoked) | None => OutNotice::Revoked,
        };
        self.lose_access(notice, ending_step);
    }

    /// The server says this Mac has no access any more: the team disks off
    /// the Mac first, then the Keychain. `ending_step` when the step that
    /// learned it owns `busy`.
    fn lose_access(&self, notice: OutNotice, ending_step: bool) {
        let marked = self.update(|inner| {
            if inner.busy.is_none() || ending_step {
                inner.busy = Some(Busy::Clearing);
                true
            } else {
                false
            }
        });
        let disks = self.clear_disks();
        let cleared = self.clear_keychain();
        self.update(|inner| {
            if marked {
                inner.busy = None;
            }
            inner.record = None;
            inner.pending = None;
            inner.credentials = None;
            inner.config = None;
            inner.reachable = true;
            inner.phase = Phase::SignedOut {
                notice: Some(notice),
            };
            inner.error = match (cleared, disks) {
                (Err(e), _) => Some(AuthError::Keychain {
                    detail: e.to_string(),
                }),
                (_, Err(detail)) => Some(AuthError::DisksNotCleared { detail }),
                _ => None,
            };
        });
    }

    // Sign-out and settings.

    /// "Esci": the server revokes this Mac (`signed_out`), then the team disks
    /// and the Keychain are cleared. `local_only` clears them without the
    /// server, after a failed attempt: the Mac then stays in the admin's list
    /// until revoked there.
    pub fn sign_out(self: &Arc<Self>, local_only: bool) -> Result<AuthView, Refused> {
        self.begin(Busy::SigningOut, |i| i.record.is_some())?;
        let record = self.lock().record.clone();
        let Some(record) = record else {
            return Ok(self.update(|inner| {
                inner.busy = None;
                Self::compose(inner)
            }));
        };
        if !local_only {
            match self.api.sign_out(&record.server, &record.device_token) {
                Ok(()) => {}
                // Already revoked or unknown: nothing left to do on the server.
                Err(e) if e.is_unauthorized() || e.code() == Some("invalid_state") => {}
                Err(e) => {
                    return Ok(self.fail(AuthError::SignOutFailed {
                        detail: e.to_string(),
                    }));
                }
            }
        }
        let disks = self.clear_disks();
        let cleared = self.clear_keychain();
        Ok(self.update(|inner| {
            inner.busy = None;
            inner.record = None;
            inner.credentials = None;
            inner.config = None;
            inner.reachable = true;
            inner.phase = Phase::SignedOut {
                notice: Some(OutNotice::SignedOut),
            };
            inner.error = match (cleared, disks) {
                (Err(e), _) => Some(AuthError::Keychain {
                    detail: e.to_string(),
                }),
                (_, Err(detail)) => Some(AuthError::DisksNotCleared { detail }),
                _ => None,
            };
            Self::compose(inner)
        }))
    }

    /// Changes the server for the next sign-in; None goes back to the default.
    pub fn set_server(&self, input: Option<&str>) -> Result<AuthView, Refused> {
        let mut inner = self.lock();
        if inner.busy.is_some() {
            return Err(Refused::Busy);
        }
        if inner.record.is_some() || matches!(inner.phase, Phase::CodeSent { .. }) {
            return Err(Refused::State);
        }
        let normalized = match input {
            None => Ok(server::DEFAULT_SERVER.to_string()),
            Some(s) => server::normalize(s),
        };
        match normalized {
            Err(e) => {
                inner.error = Some(AuthError::InvalidServer {
                    reason: match e {
                        server::ServerError::NotAnAddress => "not_an_address",
                        server::ServerError::InsecureRemote => "insecure",
                        server::ServerError::Extra => "extra",
                    },
                });
            }
            Ok(url) => {
                let stored = (url != server::DEFAULT_SERVER).then(|| url.clone());
                match self.prefs.update(|p| p.server_url = stored) {
                    Ok(()) => {
                        inner.configured_server = url;
                        inner.pending = None;
                        inner.error = None;
                    }
                    Err(e) => {
                        inner.error = Some(AuthError::Settings {
                            detail: e.to_string(),
                        })
                    }
                }
            }
        }
        self.publish(&inner);
        Ok(Self::compose(&inner))
    }

    // The worker.

    /// One pass of the worker: polls a waiting request, retries registration,
    /// sends the heartbeat, each when due.
    pub fn tick(&self) {
        enum Due {
            Poll(String, String),
            Register,
            /// The configuration, then the heartbeat when it is due too: a
            /// failing configuration never holds the heartbeat back.
            Config(bool),
            Beat,
            Clear,
            Nothing,
        }
        let due = {
            let mut guard = self.lock();
            let inner = &mut *guard;
            let now = Instant::now();
            let since = |t: Option<Instant>, every: Duration| {
                t.is_none_or(|t| now.duration_since(t) >= every)
            };
            if inner.busy.is_some() {
                Due::Nothing
            } else {
                match (&inner.phase, inner.pending.as_mut()) {
                    (Phase::CodeSent { .. }, Some(p))
                        if since(Some(p.last_poll), self.timing.poll) =>
                    {
                        p.last_poll = now;
                        Due::Poll(p.server.clone(), p.request_id.clone())
                    }
                    (Phase::Registering { .. }, _)
                        if since(inner.last_register, self.timing.register_retry) =>
                    {
                        inner.busy = Some(Busy::Registering);
                        Due::Register
                    }
                    (Phase::SignedIn { .. }, _)
                        if inner.config_due
                            && since(inner.last_config, self.timing.config_retry) =>
                    {
                        Due::Config(since(inner.last_beat, self.timing.heartbeat))
                    }
                    (Phase::SignedIn { .. }, _)
                        if since(inner.last_beat, self.timing.heartbeat) =>
                    {
                        Due::Beat
                    }
                    (Phase::SignedOut { .. } | Phase::Ended { .. }, _)
                        if inner.clear_owed && since(inner.last_clear, self.timing.clear_retry) =>
                    {
                        Due::Clear
                    }
                    _ => Due::Nothing,
                }
            }
        };
        match due {
            Due::Poll(server, id) => self.poll(&server, &id),
            Due::Register => {
                self.update(|_| {});
                self.register_now();
            }
            Due::Config(beat) => {
                self.fetch_config();
                if beat && matches!(self.lock().phase, Phase::SignedIn { .. }) {
                    self.heartbeat();
                }
            }
            Due::Beat => self.heartbeat(),
            Due::Clear => {
                let result = self.clear_disks();
                self.update(|inner| match result {
                    Ok(()) => {
                        if matches!(inner.error, Some(AuthError::DisksNotCleared { .. })) {
                            inner.error = None;
                        }
                    }
                    Err(detail) => inner.error = Some(AuthError::DisksNotCleared { detail }),
                });
            }
            Due::Nothing => {}
        }
    }

    fn poll(&self, server: &str, request_id: &str) {
        let answer = self.api.sign_in_status(server, request_id);
        let mut inner = self.lock();
        let still = inner.busy.is_none()
            && matches!(inner.phase, Phase::CodeSent { .. })
            && inner
                .pending
                .as_ref()
                .is_some_and(|p| p.request_id == request_id);
        if !still {
            return;
        }
        let end = Self::ended_for(inner.pending.as_ref());
        let mut changed = true;
        match answer.as_deref() {
            Ok("pending") => {
                changed = matches!(inner.error, Some(AuthError::Unreachable { .. }));
                if changed {
                    inner.error = None;
                }
            }
            Ok("completed") => {
                let elsewhere = self.timing.elsewhere_after;
                if let Some(p) = inner.pending.as_mut() {
                    match p.completed_seen {
                        None => {
                            p.completed_seen = Some(Instant::now());
                            changed = false;
                        }
                        Some(t) if t.elapsed() >= elsewhere => {
                            inner.phase = end(EndReason::Elsewhere);
                            inner.pending = None;
                        }
                        Some(_) => changed = false,
                    }
                }
            }
            Ok("locked") => {
                inner.phase = end(EndReason::Locked);
                inner.pending = None;
            }
            Ok(_) => {
                inner.phase = end(EndReason::Expired);
                inner.pending = None;
            }
            Err(e) if e.code() == Some("not_found") => {
                inner.phase = end(EndReason::Expired);
                inner.pending = None;
            }
            Err(e @ ApiError::Network(_)) => inner.error = Some(unreachable(e)),
            Err(e) => inner.error = Some(server_error(e)),
        }
        if changed {
            self.publish(&inner);
        }
    }

    /// Starts the worker thread; later calls do nothing.
    pub fn start_worker(self: &Arc<Self>) {
        if self.worker.swap(true, Ordering::SeqCst) {
            return;
        }
        let account = Arc::downgrade(self);
        let tick = self.timing.tick;
        thread::spawn(move || {
            while let Some(account) = account.upgrade() {
                account.tick();
                drop(account);
                thread::sleep(tick);
            }
        });
    }
}

/// The signed-in phase for a record.
fn signed_in(r: &DeviceRecord) -> Phase {
    Phase::SignedIn {
        email: r.email.clone(),
        device_name: r.device_name.clone(),
        organization: r.organization.clone(),
        user_name: r.user_name.clone(),
    }
}

#[cfg(test)]
mod e2e;
#[cfg(test)]
mod e2e_engine;
#[cfg(test)]
mod tests;
