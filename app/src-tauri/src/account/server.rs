//! The control plane's address: the build-time default and the one a
//! self-hosted team types in Settings, normalized to scheme, host, port and
//! an optional path. The API lives under `/api/v1` of that address.

use url::{Host, Url};

/// The address a build signs in to unless Settings says otherwise:
/// `FLOPPY_SERVER_URL` at build time, else the local development stack.
pub const DEFAULT_SERVER: &str = match option_env!("FLOPPY_SERVER_URL") {
    Some(url) => url,
    None => "http://127.0.0.1:4507",
};

/// The API's path under the server address (server/src/floppy_server/paths.py).
pub const API_PREFIX: &str = "/api/v1";

/// Why an address typed in Settings was refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum ServerError {
    #[error("not a web address")]
    NotAnAddress,
    /// Plain http carries the device token in clear: only this Mac may use it.
    #[error("plain http only for this Mac")]
    InsecureRemote,
    #[error("no user, query or fragment in a server address")]
    Extra,
}

fn is_loopback(host: Option<Host<&str>>) -> bool {
    match host {
        Some(Host::Domain(d)) => d.eq_ignore_ascii_case("localhost"),
        Some(Host::Ipv4(ip)) => ip.is_loopback(),
        Some(Host::Ipv6(ip)) => ip.is_loopback(),
        None => false,
    }
}

/// The address in its stored form: `https://host[:port][/path]`, no trailing
/// slash, without the `/api/v1` someone may have pasted. A bare host gets https.
pub fn normalize(input: &str) -> Result<String, ServerError> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Err(ServerError::NotAnAddress);
    }
    let with_scheme = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("https://{trimmed}")
    };
    let url = Url::parse(&with_scheme).map_err(|_| ServerError::NotAnAddress)?;
    if url.host().is_none() {
        return Err(ServerError::NotAnAddress);
    }
    match url.scheme() {
        "https" => {}
        "http" if is_loopback(url.host()) => {}
        "http" => return Err(ServerError::InsecureRemote),
        _ => return Err(ServerError::NotAnAddress),
    }
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(ServerError::Extra);
    }
    let mut path = url.path().trim_end_matches('/').to_string();
    if let Some(stripped) = path.strip_suffix(API_PREFIX) {
        path = stripped.to_string();
    }
    let origin = url.origin().ascii_serialization();
    Ok(format!("{origin}{path}"))
}

/// The full URL of an API path (`/auth/app/sign-in`) on a normalized server.
pub fn endpoint(server: &str, path: &str) -> String {
    format!("{server}{API_PREFIX}{path}")
}

/// The host and port alone, for the UI.
pub fn display(server: &str) -> String {
    Url::parse(server)
        .ok()
        .and_then(|u| {
            u.host_str().map(|h| match u.port() {
                Some(p) => format!("{h}:{p}"),
                None => h.to_string(),
            })
        })
        .unwrap_or_else(|| server.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_what_people_paste() {
        assert_eq!(
            normalize(" floppy.studio.it ").unwrap(),
            "https://floppy.studio.it"
        );
        assert_eq!(
            normalize("https://Floppy.Studio.it/").unwrap(),
            "https://floppy.studio.it"
        );
        assert_eq!(
            normalize("https://studio.it/floppy/api/v1/").unwrap(),
            "https://studio.it/floppy"
        );
        assert_eq!(
            normalize("http://127.0.0.1:4507").unwrap(),
            "http://127.0.0.1:4507"
        );
        assert_eq!(
            normalize("http://localhost:4507/api/v1").unwrap(),
            "http://localhost:4507"
        );
    }

    #[test]
    fn refuses_clear_text_off_this_mac_and_junk() {
        assert_eq!(
            normalize("http://floppy.studio.it"),
            Err(ServerError::InsecureRemote)
        );
        assert_eq!(normalize(""), Err(ServerError::NotAnAddress));
        assert_eq!(normalize("ftp://x.it"), Err(ServerError::NotAnAddress));
        assert_eq!(normalize("https://user:pw@x.it"), Err(ServerError::Extra));
        assert_eq!(normalize("https://x.it/?a=1"), Err(ServerError::Extra));
    }

    #[test]
    fn the_default_is_a_valid_address() {
        assert_eq!(normalize(DEFAULT_SERVER).unwrap(), DEFAULT_SERVER);
    }

    #[test]
    fn builds_endpoints_and_display_names() {
        assert_eq!(
            endpoint("https://x.it/floppy", "/device/register"),
            "https://x.it/floppy/api/v1/device/register"
        );
        assert_eq!(display("http://127.0.0.1:4507"), "127.0.0.1:4507");
        assert_eq!(display("https://floppy.studio.it/x"), "floppy.studio.it");
    }
}
