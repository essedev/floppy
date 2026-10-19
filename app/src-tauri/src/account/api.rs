//! The control plane's endpoints the app uses, exactly as the server exposes
//! them (server/src/floppy_server/auth/router.py, device/router.py): the app's
//! sign-in under `/auth/app/*` and the Mac's own calls under `/device/*`,
//! authenticated by `Authorization: Bearer <device token>`. Errors come back as
//! `{code, message, details}` (errors.py); a device's 401 carries
//! `details.reason`. The shapes follow the Pydantic models of
//! device/service.py.

use super::server;
use crate::engine::{Op, TrashKind};
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// How long one call may take before the server counts as unreachable.
pub const TIMEOUT: Duration = Duration::from_secs(15);

/// Why a call failed.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ApiError {
    /// No answer: DNS, connection, TLS or timeout.
    #[error("the server did not answer: {0}")]
    Network(String),
    /// An answer with an error status and the contract's code.
    #[error("{status} {code}: {message}")]
    Status {
        status: u16,
        code: String,
        message: String,
        /// `details.attemptsLeft` of `invalid_code`.
        attempts_left: Option<u32>,
        /// `details.retryAfter` (or the header) of `rate_limited`, seconds.
        retry_after: Option<u64>,
        /// `details.reason` of a device's 401 (`DeviceRefusal`).
        reason: Option<String>,
    },
    /// An answer this app cannot read: not the control plane, or a newer one.
    #[error("unexpected answer: {0}")]
    Unexpected(String),
}

impl ApiError {
    /// The contract code, when the server sent one.
    pub fn code(&self) -> Option<&str> {
        match self {
            ApiError::Status { code, .. } => Some(code),
            _ => None,
        }
    }

    /// 401: the device token is unknown, revoked or not registered.
    pub fn is_unauthorized(&self) -> bool {
        matches!(self, ApiError::Status { status: 401, .. })
    }

    /// Why the server refused this Mac's token, for a 401.
    pub fn refusal(&self) -> Option<DeviceRefusal> {
        match self {
            ApiError::Status {
                status: 401,
                reason,
                ..
            } => Some(match reason.as_deref() {
                Some("token_unknown") => DeviceRefusal::TokenUnknown,
                Some("device_signed_out") => DeviceRefusal::SignedOut,
                Some("device_not_registered") => DeviceRefusal::NotRegistered,
                // `device_revoked`, and a server from before the reasons.
                _ => DeviceRefusal::Revoked,
            }),
            _ => None,
        }
    }
}

/// The reasons of a device's 401 (server errors.py, `device_unauthorized`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceRefusal {
    /// The server does not know this token: the Mac was deleted, or the
    /// server is another one (or was rebuilt).
    TokenUnknown,
    /// An admin, or the person from the web, revoked this Mac.
    Revoked,
    /// This Mac signed out (`/device/sign-out`) earlier.
    SignedOut,
    /// The Mac is still `pending`: it has to register.
    NotRegistered,
}

/// A serde error without the input it choked on: it may be a secret.
pub fn shape_error(e: &serde_json::Error) -> String {
    format!(
        "{:?} at line {} column {}",
        e.classify(),
        e.line(),
        e.column()
    )
}

#[derive(Deserialize)]
struct ErrorBody {
    code: String,
    #[serde(default)]
    message: String,
    #[serde(default)]
    details: serde_json::Value,
}

/// The error for a non-2xx answer, from its status, body and `Retry-After`.
pub fn error_from(status: u16, body: &[u8], retry_header: Option<&str>) -> ApiError {
    match serde_json::from_slice::<ErrorBody>(body) {
        Ok(b) => ApiError::Status {
            status,
            attempts_left: b
                .details
                .get("attemptsLeft")
                .and_then(|v| v.as_u64())
                .map(|n| n as u32),
            retry_after: b
                .details
                .get("retryAfter")
                .and_then(|v| v.as_u64())
                .or_else(|| retry_header.and_then(|h| h.trim().parse().ok())),
            reason: b
                .details
                .get("reason")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            code: b.code,
            message: b.message,
        },
        Err(_) => ApiError::Unexpected(format!("HTTP {status} without the error shape")),
    }
}

/// `POST /auth/app/sign-in`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartRequest {
    pub email: String,
    pub code_challenge: String,
    pub redirect_uri: String,
    pub device_name: String,
}

/// The pending request; the same answer whether or not the address is a member.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartResponse {
    pub request_id: String,
    pub email: String,
    pub expires_at: String,
    pub resend_at: String,
}

/// What proves the request at the token exchange, with the verifier.
#[derive(Clone, PartialEq, Eq)]
pub enum Proof {
    /// The 6-digit code typed in the app.
    Code(String),
    /// The one-time code the link sent to the loopback.
    Link(String),
}

impl std::fmt::Debug for Proof {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Proof::Code(_) => "Proof::Code(..)",
            Proof::Link(_) => "Proof::Link(..)",
        })
    }
}

/// The new Mac (`pending` until it registers) and its token, returned once.
#[derive(Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Grant {
    pub device_id: String,
    pub device_token: String,
}

impl std::fmt::Debug for Grant {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Grant")
            .field("device_id", &self.device_id)
            .finish_non_exhaustive()
    }
}

/// What the app reports when it registers and in each heartbeat.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Versions {
    pub app_version: String,
    pub macos_version: String,
}

/// The heartbeat's state: `revoked` means this Mac lost its access.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Beat {
    Active,
    Revoked,
}

/// Read-only or read-write, per person and disk (DECISIONS #23).
pub use crate::engine::Access;

/// The organization the Mac belongs to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrganizationRef {
    pub id: String,
    pub name: String,
}

/// The person the Mac belongs to; `name` falls back to the address.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UserRef {
    pub email: String,
    pub name: String,
}

/// A disk this Mac may see, as it mounts it: `metaUrl` is its instance and
/// database without credentials; `read` is a `--read-only` mount; `region`
/// the bucket's S3 region, which the mount signs with (an older server
/// leaves it out).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DiskConfig {
    pub id: String,
    pub name: String,
    pub volume_name: String,
    pub access: Access,
    pub meta_url: String,
    #[serde(default)]
    pub region: Option<String>,
    pub cache_expire_days: u32,
    pub mount_path: String,
}

/// `GET /device/config`: everything to mount, no secret. `s3AccessKeyTail`
/// is the end of this Mac's own key (managed), to check the Keychain's.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceConfig {
    /// The Mac's Valkey user; also in the register answer, from which the
    /// app keeps it, so an older server may leave it out.
    #[serde(default)]
    pub valkey_username: Option<String>,
    pub storage_mode: String,
    pub s3_access_key_tail: Option<String>,
    /// PEM of the private CA of the disks' metadata TLS; null for a public one.
    #[serde(default)]
    pub meta_ca: Option<String>,
    pub organization: OrganizationRef,
    pub user: UserRef,
    pub disks: Vec<DiskConfig>,
}

/// This Mac's own S3 key (managed mode).
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct S3KeyOut {
    pub access_key_id: String,
    pub secret_key: String,
}

/// `POST /device/register`, returned once: the Valkey user and password (the
/// same on every disk's instance), the S3 key in managed mode, the disks.
#[derive(Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RegisterResponse {
    pub valkey_username: String,
    pub valkey_password: String,
    pub storage_mode: String,
    pub s3: Option<S3KeyOut>,
    pub organization: OrganizationRef,
    pub user: UserRef,
    pub disks: Vec<DiskConfig>,
}

impl std::fmt::Debug for RegisterResponse {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RegisterResponse")
            .field("valkey_username", &self.valkey_username)
            .field("storage_mode", &self.storage_mode)
            .field("s3", &self.s3.is_some())
            .field("disks", &self.disks.len())
            .finish_non_exhaustive()
    }
}

impl RegisterResponse {
    /// The same facts as `GET /device/config` would give now.
    pub fn config(&self) -> DeviceConfig {
        DeviceConfig {
            valkey_username: Some(self.valkey_username.clone()),
            storage_mode: self.storage_mode.clone(),
            s3_access_key_tail: self.s3.as_ref().map(|k| key_tail(&k.access_key_id)),
            // Not in the register answer: the configuration read right after brings it.
            meta_ca: None,
            organization: self.organization.clone(),
            user: self.user.clone(),
            disks: self.disks.clone(),
        }
    }
}

/// The end of an access key as the server keeps it (`right(access_key_id, 5)`).
pub fn key_tail(access_key_id: &str) -> String {
    let chars: Vec<char> = access_key_id.chars().collect();
    chars[chars.len().saturating_sub(5)..].iter().collect()
}

/// Where this Mac's mounts read their metadata from.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplicaReport {
    /// `replica`, `master` or `offline`.
    pub mode: String,
    pub lag_ms: Option<u64>,
}

/// What the Mac reports every minute.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HeartbeatRequest {
    #[serde(flatten)]
    pub versions: Versions,
    pub replica: Option<ReplicaReport>,
    pub s3_access_key_tail: Option<String>,
    pub mounted_disk_ids: Vec<String>,
}

/// A disk the Mac may see now, and how.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DiskAccessRef {
    pub id: String,
    pub access: Access,
}

/// The heartbeat's answer: the state and what the Mac may see now.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeartbeatAnswer {
    pub state: Beat,
    #[serde(default)]
    pub disks: Vec<DiskAccessRef>,
}

/// The calls the account makes; `Http` in the app, fakes in the tests.
pub trait ControlPlane: Send + Sync {
    fn start_sign_in(&self, server: &str, req: &StartRequest) -> Result<StartResponse, ApiError>;
    /// `pending`, `completed`, `expired` or `locked`.
    fn sign_in_status(&self, server: &str, request_id: &str) -> Result<String, ApiError>;
    fn exchange(&self, server: &str, verifier: &str, proof: &Proof) -> Result<Grant, ApiError>;
    fn register(
        &self,
        server: &str,
        token: &str,
        v: &Versions,
    ) -> Result<RegisterResponse, ApiError>;
    fn config(&self, server: &str, token: &str) -> Result<DeviceConfig, ApiError>;
    fn heartbeat(
        &self,
        server: &str,
        token: &str,
        beat: &HeartbeatRequest,
    ) -> Result<HeartbeatAnswer, ApiError>;
    fn sign_out(&self, server: &str, token: &str) -> Result<(), ApiError>;
}

/// The control plane over HTTPS (plain http only on this Mac, see `server`).
pub struct Http {
    agent: ureq::Agent,
}

impl Http {
    /// A client that sends `user_agent` and trusts the Mac's own certificates.
    pub fn new(user_agent: &str) -> Self {
        let tls = ureq::tls::TlsConfig::builder()
            .root_certs(ureq::tls::RootCerts::PlatformVerifier)
            .build();
        let agent = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .timeout_global(Some(TIMEOUT))
            // A redirect could carry the token to another host: none is followed.
            .max_redirects(0)
            .user_agent(user_agent)
            .tls_config(tls)
            .build()
            .new_agent();
        Http { agent }
    }

    fn send(
        &self,
        method: &str,
        url: &str,
        token: Option<&str>,
        body: Option<serde_json::Value>,
    ) -> Result<(u16, Vec<u8>), ApiError> {
        let network = |e: ureq::Error| ApiError::Network(e.to_string());
        let response = match method {
            "GET" => {
                let mut req = self.agent.get(url).header("Accept", "application/json");
                if let Some(t) = token {
                    req = req.header("Authorization", format!("Bearer {t}"));
                }
                req.call().map_err(network)?
            }
            _ => {
                let mut req = self
                    .agent
                    .post(url)
                    .header("Accept", "application/json")
                    // Mail to an address with no team follows this language.
                    .header("Accept-Language", "it");
                if let Some(t) = token {
                    req = req.header("Authorization", format!("Bearer {t}"));
                }
                match body {
                    Some(b) => req.send_json(b).map_err(network)?,
                    None => req.send_empty().map_err(network)?,
                }
            }
        };
        let status = response.status().as_u16();
        let retry = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .map(str::to_string);
        let bytes = response.into_body().read_to_vec().map_err(network)?;
        if (200..300).contains(&status) {
            Ok((status, bytes))
        } else {
            Err(error_from(status, &bytes, retry.as_deref()))
        }
    }

    fn json<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> Result<T, ApiError> {
        serde_json::from_slice(bytes).map_err(|e| ApiError::Unexpected(shape_error(&e)))
    }

    fn value<T: Serialize>(v: &T) -> Result<serde_json::Value, ApiError> {
        serde_json::to_value(v).map_err(|e| ApiError::Unexpected(shape_error(&e)))
    }
}

impl ControlPlane for Http {
    fn start_sign_in(&self, server: &str, req: &StartRequest) -> Result<StartResponse, ApiError> {
        let url = server::endpoint(server, "/auth/app/sign-in");
        let (_, body) = self.send("POST", &url, None, Some(Self::value(req)?))?;
        Self::json(&body)
    }

    fn sign_in_status(&self, server: &str, request_id: &str) -> Result<String, ApiError> {
        #[derive(Deserialize)]
        struct Status {
            status: String,
        }
        let url = server::endpoint(server, &format!("/auth/sign-in/{request_id}"));
        let (_, body) = self.send("GET", &url, None, None)?;
        Ok(Self::json::<Status>(&body)?.status)
    }

    fn exchange(&self, server: &str, verifier: &str, proof: &Proof) -> Result<Grant, ApiError> {
        let body = match proof {
            Proof::Code(code) => serde_json::json!({ "codeVerifier": verifier, "code": code }),
            Proof::Link(auth) => {
                serde_json::json!({ "codeVerifier": verifier, "authorizationCode": auth })
            }
        };
        let url = server::endpoint(server, "/auth/app/token");
        let (_, body) = self.send("POST", &url, None, Some(body))?;
        Self::json(&body)
    }

    fn register(
        &self,
        server: &str,
        token: &str,
        v: &Versions,
    ) -> Result<RegisterResponse, ApiError> {
        let url = server::endpoint(server, "/device/register");
        let (_, body) = self.send("POST", &url, Some(token), Some(Self::value(v)?))?;
        Self::json(&body)
    }

    fn config(&self, server: &str, token: &str) -> Result<DeviceConfig, ApiError> {
        let url = server::endpoint(server, "/device/config");
        let (_, body) = self.send("GET", &url, Some(token), None)?;
        Self::json(&body)
    }

    fn heartbeat(
        &self,
        server: &str,
        token: &str,
        beat: &HeartbeatRequest,
    ) -> Result<HeartbeatAnswer, ApiError> {
        let url = server::endpoint(server, "/device/heartbeat");
        let (_, body) = self.send("POST", &url, Some(token), Some(Self::value(beat)?))?;
        Self::json(&body)
    }

    fn sign_out(&self, server: &str, token: &str) -> Result<(), ApiError> {
        let url = server::endpoint(server, "/device/sign-out");
        self.send("POST", &url, Some(token), None).map(|_| ())
    }
}

/// Who did something on a disk: the person's name, and whether it is this
/// Mac's own person.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PersonRef {
    pub name: String,
    pub you: bool,
}

/// One of this Mac's events as `POST /device/activity` takes it; times are
/// Unix seconds, which the server reads as UTC.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EventIn {
    pub local_id: u64,
    pub at: f64,
    pub disk_id: String,
    pub op: Op,
    pub kind: TrashKind,
    pub path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
    pub size: Option<u64>,
}

/// A file this Mac is writing on a team disk.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WritingIn {
    pub disk_id: String,
    pub path: String,
    pub op: Op,
    pub since: f64,
    pub size: Option<u64>,
}

/// `POST /device/activity`: the new events, what is being written, the cursor.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActivityRequest {
    pub events: Vec<EventIn>,
    pub writing: Vec<WritingIn>,
    pub after: Option<String>,
}

/// An event on a disk as the server keeps it; `at` is ISO 8601 in UTC.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemoteEvent {
    pub id: String,
    pub disk_id: String,
    pub at: String,
    pub op: Op,
    pub kind: TrashKind,
    pub path: String,
    pub to: Option<String>,
    pub size: Option<u64>,
    pub by: PersonRef,
    /// Reported by this Mac: `localId` is its own record's id.
    pub this_device: bool,
    pub local_id: u64,
}

/// A file another Mac is writing on a disk this Mac sees.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemoteWriting {
    pub disk_id: String,
    pub path: String,
    pub op: Op,
    pub since: String,
    pub size: Option<u64>,
    pub by: PersonRef,
}

/// What changed on this Mac's disks since its cursor, and what others write.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ActivityAnswer {
    pub events: Vec<RemoteEvent>,
    pub cursor: String,
    pub more: bool,
    pub writing: Vec<RemoteWriting>,
}

/// `GET /device/activity`: a page of one disk's history, newest first.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ActivityPage {
    pub events: Vec<RemoteEvent>,
    pub before: Option<String>,
}

/// A trash entry, for `POST /device/activity/deletes`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DeleteQuery {
    pub path: String,
    pub at: f64,
}

/// `POST /device/activity/deletes`: who deleted each of a disk's entries.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeletesRequest {
    pub disk_id: String,
    pub items: Vec<DeleteQuery>,
}

#[derive(Deserialize)]
struct DeletesAnswer {
    items: Vec<Option<PersonRef>>,
}

/// The activity calls (DECISIONS #27); `Http` in the app, fakes in the tests.
pub trait ActivityApi: Send + Sync {
    fn activity(
        &self,
        server: &str,
        token: &str,
        report: &ActivityRequest,
    ) -> Result<ActivityAnswer, ApiError>;
    fn activity_history(
        &self,
        server: &str,
        token: &str,
        disk_id: &str,
        before: Option<&str>,
    ) -> Result<ActivityPage, ApiError>;
    /// Who deleted each entry, in the order asked; None where no event says.
    fn activity_deletes(
        &self,
        server: &str,
        token: &str,
        req: &DeletesRequest,
    ) -> Result<Vec<Option<PersonRef>>, ApiError>;
}

/// How many events a history page asks for.
pub const HISTORY_PAGE: u32 = 100;

impl ActivityApi for Http {
    fn activity(
        &self,
        server: &str,
        token: &str,
        report: &ActivityRequest,
    ) -> Result<ActivityAnswer, ApiError> {
        let url = server::endpoint(server, "/device/activity");
        let (_, body) = self.send("POST", &url, Some(token), Some(Self::value(report)?))?;
        Self::json(&body)
    }

    fn activity_history(
        &self,
        server: &str,
        token: &str,
        disk_id: &str,
        before: Option<&str>,
    ) -> Result<ActivityPage, ApiError> {
        let mut url = url::Url::parse(&server::endpoint(server, "/device/activity"))
            .map_err(|e| ApiError::Unexpected(e.to_string()))?;
        {
            let mut q = url.query_pairs_mut();
            q.append_pair("diskId", disk_id)
                .append_pair("limit", &HISTORY_PAGE.to_string());
            if let Some(b) = before {
                q.append_pair("before", b);
            }
        }
        let (_, body) = self.send("GET", url.as_str(), Some(token), None)?;
        Self::json(&body)
    }

    fn activity_deletes(
        &self,
        server: &str,
        token: &str,
        req: &DeletesRequest,
    ) -> Result<Vec<Option<PersonRef>>, ApiError> {
        let url = server::endpoint(server, "/device/activity/deletes");
        let (_, body) = self.send("POST", &url, Some(token), Some(Self::value(req)?))?;
        Ok(Self::json::<DeletesAnswer>(&body)?.items)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The activity's shapes exactly as the server's models serialize them.
    #[test]
    fn speaks_the_activity_contract() {
        let sent = serde_json::to_value(ActivityRequest {
            events: vec![EventIn {
                local_id: 1792395950001,
                at: 1792395950.25,
                disk_id: "d1".into(),
                op: Op::Deleted,
                kind: TrashKind::File,
                path: "Girato/a.mov".into(),
                to: None,
                size: Some(12),
            }],
            writing: vec![WritingIn {
                disk_id: "d1".into(),
                path: "Girato/b.mov".into(),
                op: Op::Added,
                since: 1792395900.0,
                size: None,
            }],
            after: None,
        })
        .unwrap();
        assert_eq!(
            sent,
            serde_json::json!({"events":[{"localId":1792395950001_u64,"at":1792395950.25,"diskId":"d1",
                "op":"deleted","kind":"file","path":"Girato/a.mov","size":12}],
                "writing":[{"diskId":"d1","path":"Girato/b.mov","op":"added","since":1792395900.0,"size":null}],
                "after":null})
        );
        let answer: ActivityAnswer = serde_json::from_str(
            r#"{"events":[{"id":"0192f0c4-0000-7000-8000-000000000009","diskId":"d1","at":"2026-10-19T10:00:00.250000Z",
            "op":"renamed","kind":"folder","path":"Backstage","to":"Backstage Tosca","size":null,
            "by":{"name":"Marco","you":false},"thisDevice":false,"localId":7}],
            "cursor":"abc","more":false,
            "writing":[{"diskId":"d1","path":"Girato/c.mov","op":"added","since":"2026-10-19T09:59:00Z","size":512,
            "by":{"name":"Marco","you":false},"reportedAt":"2026-10-19T10:00:00Z"}]}"#,
        )
        .unwrap();
        assert_eq!(answer.events[0].to.as_deref(), Some("Backstage Tosca"));
        assert_eq!(answer.writing[0].by.name, "Marco");
    }

    #[test]
    fn reads_the_error_contract() {
        let e = error_from(
            422,
            br#"{"code":"invalid_code","message":"Wrong code","details":{"attemptsLeft":3}}"#,
            None,
        );
        assert_eq!(e.code(), Some("invalid_code"));
        assert!(matches!(
            e,
            ApiError::Status {
                attempts_left: Some(3),
                ..
            }
        ));
        let limited = error_from(
            429,
            br#"{"code":"rate_limited","message":"x","details":{}}"#,
            Some("17"),
        );
        assert!(matches!(
            limited,
            ApiError::Status {
                retry_after: Some(17),
                ..
            }
        ));
        assert!(
            error_from(401, br#"{"code":"unauthorized","message":"x"}"#, None).is_unauthorized()
        );
        assert!(matches!(
            error_from(502, b"<html>bad gateway</html>", None),
            ApiError::Unexpected(_)
        ));
    }

    #[test]
    fn reads_the_device_refusal_reasons() {
        let refusal = |reason: &str| {
            error_from(
                401,
                format!(
                    r#"{{"code":"unauthorized","message":"x","details":{{"reason":"{reason}"}}}}"#
                )
                .as_bytes(),
                None,
            )
            .refusal()
        };
        assert_eq!(refusal("token_unknown"), Some(DeviceRefusal::TokenUnknown));
        assert_eq!(refusal("device_revoked"), Some(DeviceRefusal::Revoked));
        assert_eq!(refusal("device_signed_out"), Some(DeviceRefusal::SignedOut));
        assert_eq!(
            refusal("device_not_registered"),
            Some(DeviceRefusal::NotRegistered)
        );
        // A server from before the reasons: revoked, as the app always read it.
        let bare = error_from(401, br#"{"code":"unauthorized","message":"x"}"#, None);
        assert_eq!(bare.refusal(), Some(DeviceRefusal::Revoked));
        let other = error_from(403, br#"{"code":"forbidden","message":"x"}"#, None);
        assert_eq!(other.refusal(), None);
    }

    /// The answers exactly as the server's Pydantic models serialize them.
    #[test]
    fn parses_the_device_answers() {
        let register: RegisterResponse = serde_json::from_str(
            r#"{"valkeyUsername":"floppy_d_0192f0c47d1e7a409e523c1d2b9a0f11","valkeyPassword":"pw-secret",
            "storageMode":"managed","s3":{"accessKeyId":"GK1234567890abcdef","secretKey":"sk-secret"},
            "organization":{"id":"0192f0c4-0000-7000-8000-000000000001","name":"Studio Esempio"},
            "user":{"email":"giulia@studio.test","name":"Giulia Ferri"},
            "disks":[{"id":"0192f0c4-7d1e-7a40-9e52-3c1d2b9a0f11","name":"Archivio 2025","volumeName":"archivio-2025",
            "access":"read","metaUrl":"rediss://archivio-2025.meta.example.com:6443/1","region":"garage","cacheExpireDays":30,
            "mountPath":"/Volumes/Floppy/Archivio 2025"}]}"#,
        )
        .unwrap();
        let debug = format!("{register:?}");
        assert!(!debug.contains("pw-secret") && !debug.contains("sk-secret"));
        let config = register.config();
        assert_eq!(config.s3_access_key_tail.as_deref(), Some("bcdef"));
        assert_eq!(config.organization.name, "Studio Esempio");
        assert_eq!(config.disks[0].access, Access::Read);
        assert_eq!(config.disks[0].region.as_deref(), Some("garage"));

        let config: DeviceConfig = serde_json::from_str(
            r#"{"valkeyUsername":"floppy_d_1","storageMode":"manual","s3AccessKeyTail":null,
            "metaCa":"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n",
            "organization":{"id":"o","name":"Studio"},"user":{"email":"a@b.it","name":"a@b.it"},"disks":[]}"#,
        )
        .unwrap();
        assert_eq!(config.storage_mode, "manual");
        assert_eq!(config.valkey_username.as_deref(), Some("floppy_d_1"));
        assert!(config.meta_ca.unwrap().starts_with("-----BEGIN"));

        let beat: HeartbeatAnswer =
            serde_json::from_str(r#"{"state":"active","disks":[{"id":"d1","access":"write"}]}"#)
                .unwrap();
        assert_eq!(beat.disks[0].access, Access::Write);
        let revoked: HeartbeatAnswer =
            serde_json::from_str(r#"{"state":"revoked","disks":[]}"#).unwrap();
        assert_eq!(revoked.state, Beat::Revoked);

        let sent = serde_json::to_value(HeartbeatRequest {
            versions: Versions {
                app_version: "0.1.0".into(),
                macos_version: "15.5".into(),
            },
            replica: Some(ReplicaReport {
                mode: "offline".into(),
                lag_ms: None,
            }),
            s3_access_key_tail: None,
            mounted_disk_ids: vec!["d1".into()],
        })
        .unwrap();
        assert_eq!(
            sent,
            serde_json::json!({"appVersion":"0.1.0","macosVersion":"15.5",
                "replica":{"mode":"offline","lagMs":null},"s3AccessKeyTail":null,"mountedDiskIds":["d1"]})
        );
    }

    #[test]
    fn shape_errors_never_carry_the_value() {
        let e = serde_json::from_str::<Grant>(r#"{"deviceId":1,"deviceToken":"tok-secret"}"#)
            .unwrap_err();
        assert!(!shape_error(&e).contains("tok-secret"));
        assert!(!shape_error(&e).contains("invalid type"));
    }

    #[test]
    fn proofs_and_grants_hide_their_secrets_in_debug() {
        assert_eq!(
            format!("{:?}", Proof::Code("123456".into())),
            "Proof::Code(..)"
        );
        let g = Grant {
            device_id: "d1".into(),
            device_token: "tok-secret".into(),
        };
        assert!(!format!("{g:?}").contains("tok-secret"));
    }
}
