//! The control plane's endpoints the app uses, exactly as the server exposes
//! them (server/src/floppy_server/auth/router.py, device/router.py): the app's
//! sign-in under `/auth/app/*` and the Mac's own calls under `/device/*`,
//! authenticated by `Authorization: Bearer <device token>`. Errors come back as
//! `{code, message, details}` (errors.py).

use super::server;
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

/// The heartbeat's answer: `revoked` means this Mac lost its access.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Beat {
    Active,
    Revoked,
}

/// The calls the account makes; `Http` in the app, fakes in the tests.
pub trait ControlPlane: Send + Sync {
    fn start_sign_in(&self, server: &str, req: &StartRequest) -> Result<StartResponse, ApiError>;
    /// `pending`, `completed`, `expired` or `locked`.
    fn sign_in_status(&self, server: &str, request_id: &str) -> Result<String, ApiError>;
    fn exchange(&self, server: &str, verifier: &str, proof: &Proof) -> Result<Grant, ApiError>;
    /// The registration answer, raw: `credentials::keep` reads it.
    fn register(&self, server: &str, token: &str, v: &Versions) -> Result<Vec<u8>, ApiError>;
    fn heartbeat(&self, server: &str, token: &str, v: &Versions) -> Result<Beat, ApiError>;
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

    fn register(&self, server: &str, token: &str, v: &Versions) -> Result<Vec<u8>, ApiError> {
        let url = server::endpoint(server, "/device/register");
        let (_, body) = self.send("POST", &url, Some(token), Some(Self::value(v)?))?;
        Ok(body)
    }

    fn heartbeat(&self, server: &str, token: &str, v: &Versions) -> Result<Beat, ApiError> {
        #[derive(Deserialize)]
        struct Answer {
            state: Beat,
        }
        let url = server::endpoint(server, "/device/heartbeat");
        let (_, body) = self.send("POST", &url, Some(token), Some(Self::value(v)?))?;
        Ok(Self::json::<Answer>(&body)?.state)
    }

    fn sign_out(&self, server: &str, token: &str) -> Result<(), ApiError> {
        let url = server::endpoint(server, "/device/sign-out");
        self.send("POST", &url, Some(token), None).map(|_| ())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
