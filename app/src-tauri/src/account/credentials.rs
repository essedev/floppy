//! What registering this Mac hands it for its disks, and where it goes. The
//! shape follows the server's `RegisterResponse` (server/src/floppy_server/
//! device/service.py) and will change with per-disk permissions: it is parsed,
//! kept and cleared only here, and nothing in the app mounts with it yet.

use super::secrets::{SecretError, SecretStore};
use serde::{Deserialize, Serialize};

/// The Keychain item holding the disk credentials.
pub const ITEM: &str = "disk-credentials";

/// An S3 key for the mounts (`person` or `device` key scope).
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct S3Key {
    pub access_key_id: String,
    pub secret_key: String,
}

/// The Mac's Valkey user and, by key scope, its S3 key: returned once.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DiskCredentials {
    pub valkey_username: String,
    pub valkey_password: String,
    pub valkey_url: String,
    pub key_scope: String,
    pub s3: Option<S3Key>,
}

impl std::fmt::Debug for DiskCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DiskCredentials")
            .field("valkey_username", &self.valkey_username)
            .field("key_scope", &self.key_scope)
            .field("s3", &self.s3.is_some())
            .finish_non_exhaustive()
    }
}

/// Parses the registration answer and keeps it in the Keychain, replacing
/// what a previous registration left (a reissue makes the old password void).
pub fn keep(store: &dyn SecretStore, body: &[u8]) -> Result<(), KeepError> {
    let credentials: DiskCredentials =
        serde_json::from_slice(body).map_err(|e| KeepError::Shape(super::api::shape_error(&e)))?;
    let json = serde_json::to_vec(&credentials)
        .map_err(|e| KeepError::Shape(super::api::shape_error(&e)))?;
    store.set(ITEM, &json).map_err(KeepError::Keychain)
}

/// Removes the disk credentials (sign-out, revocation).
pub fn clear(store: &dyn SecretStore) -> Result<(), SecretError> {
    store.delete(ITEM)
}

/// Why the credentials could not be kept.
#[derive(Debug, thiserror::Error)]
pub enum KeepError {
    #[error("unexpected registration answer: {0}")]
    Shape(String),
    #[error(transparent)]
    Keychain(SecretError),
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::account::secrets::MemoryStore;

    const BODY: &str = r#"{"valkeyUsername":"floppy_d_1","valkeyPassword":"pw-secret","valkeyUrl":"rediss://v:6380","keyScope":"device","s3":{"accessKeyId":"AK","secretKey":"sk-secret"}}"#;

    #[test]
    fn keeps_and_clears_without_showing_secrets() {
        let store = MemoryStore::default();
        keep(&store, BODY.as_bytes()).unwrap();
        let kept: DiskCredentials =
            serde_json::from_slice(&store.get(ITEM).unwrap().unwrap()).unwrap();
        assert_eq!(kept.valkey_username, "floppy_d_1");
        assert_eq!(kept.s3.as_ref().unwrap().secret_key, "sk-secret");
        let debug = format!("{kept:?}");
        assert!(!debug.contains("pw-secret") && !debug.contains("sk-secret"));
        clear(&store).unwrap();
        assert!(store.names().is_empty());
    }

    #[test]
    fn refuses_an_answer_of_another_shape() {
        let store = MemoryStore::default();
        assert!(matches!(
            keep(&store, br#"{"valkeyUsername":"x"}"#),
            Err(KeepError::Shape(_))
        ));
        assert!(store.names().is_empty());
    }
}
