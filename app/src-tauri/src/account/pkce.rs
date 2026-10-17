//! PKCE (RFC 7636, S256) for the app's sign-in, and the random values around
//! it: the verifier never leaves this process until the token exchange, so a
//! link or code read by someone else is useless without it.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use sha2::{Digest, Sha256};

/// A URL-safe random string from `bytes` random bytes (43 characters for 32).
pub fn random_token(bytes: usize) -> Result<String, getrandom::Error> {
    let mut buf = vec![0u8; bytes];
    getrandom::fill(&mut buf)?;
    Ok(URL_SAFE_NO_PAD.encode(buf))
}

/// A new verifier: 256 random bits, 43 characters of `[A-Za-z0-9_-]`.
pub fn verifier() -> Result<String, getrandom::Error> {
    random_token(32)
}

/// The S256 challenge of a verifier: base64url of its SHA-256, no padding.
pub fn challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_the_rfc_example() {
        // RFC 7636, appendix B.
        assert_eq!(
            challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn verifiers_fit_the_server_pattern_and_differ() {
        let a = verifier().unwrap();
        let b = verifier().unwrap();
        assert_ne!(a, b);
        assert_eq!(a.len(), 43);
        assert!(
            a.chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        );
        assert_eq!(challenge(&a).len(), 43);
    }
}
