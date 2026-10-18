//! What registering this Mac hands it for its disks, and where it goes: the
//! Valkey user and password (the same on every disk's instance, DECISIONS #7)
//! and, in managed mode, the Mac's own S3 key (DECISIONS #24). They live in the
//! Keychain only, and reach the engine on the stdin of `floppy disks apply`
//! (`secrets_json`), never a file, an argument or a log.

use super::api::{DiskConfig, RegisterResponse, key_tail};
use super::secrets::{SecretError, SecretStore};
use serde::{Deserialize, Serialize};

/// The Keychain item holding the disk credentials.
pub const ITEM: &str = "disk-credentials";

/// The Mac's own S3 key (managed mode); none in manual mode, where each
/// disk's key is in its format.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct S3Key {
    pub access_key_id: String,
    pub secret_key: String,
}

/// The Mac's Valkey user and password and its S3 key: returned once.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DiskCredentials {
    pub valkey_username: String,
    pub valkey_password: String,
    pub s3: Option<S3Key>,
}

impl std::fmt::Debug for DiskCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DiskCredentials")
            .field("valkey_username", &self.valkey_username)
            .field("s3", &self.s3.as_ref().map(|k| key_tail(&k.access_key_id)))
            .finish_non_exhaustive()
    }
}

impl DiskCredentials {
    /// The credentials of a registration answer.
    pub fn from_register(r: &RegisterResponse) -> Self {
        DiskCredentials {
            valkey_username: r.valkey_username.clone(),
            valkey_password: r.valkey_password.clone(),
            s3: r.s3.as_ref().map(|k| S3Key {
                access_key_id: k.access_key_id.clone(),
                secret_key: k.secret_key.clone(),
            }),
        }
    }

    /// The end of the S3 key, as the heartbeat reports it.
    pub fn s3_tail(&self) -> Option<String> {
        self.s3.as_ref().map(|k| key_tail(&k.access_key_id))
    }

    /// The secrets for `floppy disks apply`'s stdin (docs/ARCHITECTURE.md,
    /// "Team disks"): the Valkey password, and the Mac's one S3 key for each
    /// disk when it has one (it is granted on every bucket it may see).
    pub fn secrets_json(&self, disks: &[DiskConfig]) -> Vec<u8> {
        let s3: serde_json::Map<String, serde_json::Value> = match &self.s3 {
            Some(k) => disks
                .iter()
                .map(|d| {
                    (
                        d.id.clone(),
                        serde_json::json!({
                            "access_key": k.access_key_id,
                            "secret_key": k.secret_key,
                        }),
                    )
                })
                .collect(),
            None => serde_json::Map::new(),
        };
        serde_json::to_vec(&serde_json::json!({
            "version": 1,
            "meta_password": self.valkey_password,
            "s3": s3,
        }))
        .unwrap_or_default()
    }
}

/// The credentials kept from a previous launch. An item from an older app
/// (with `valkeyUrl` and `keyScope`) still reads: unknown keys are ignored.
pub fn load(store: &dyn SecretStore) -> Result<Option<DiskCredentials>, SecretError> {
    match store.get(ITEM)? {
        None => Ok(None),
        Some(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|e| SecretError {
                message: format!("unreadable credentials: {}", super::api::shape_error(&e)),
            }),
    }
}

/// Keeps the credentials in the Keychain, replacing what a previous
/// registration left (a reissue makes the old password void).
pub fn keep(store: &dyn SecretStore, credentials: &DiskCredentials) -> Result<(), SecretError> {
    let json = serde_json::to_vec(credentials).map_err(|e| SecretError {
        message: super::api::shape_error(&e),
    })?;
    store.set(ITEM, &json)
}

/// Removes the disk credentials (sign-out, revocation).
pub fn clear(store: &dyn SecretStore) -> Result<(), SecretError> {
    store.delete(ITEM)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::account::api::Access;
    use crate::account::secrets::MemoryStore;

    fn creds(s3: bool) -> DiskCredentials {
        DiskCredentials {
            valkey_username: "floppy_d_1".into(),
            valkey_password: "pw-secret".into(),
            s3: s3.then(|| S3Key {
                access_key_id: "GK123456789".into(),
                secret_key: "sk-secret".into(),
            }),
        }
    }

    fn disk(id: &str) -> DiskConfig {
        DiskConfig {
            id: id.into(),
            name: id.into(),
            volume_name: id.into(),
            access: Access::Read,
            meta_url: "rediss://m.example.com:6443/1".into(),
            region: None,
            cache_expire_days: 30,
            mount_path: format!("/Volumes/Floppy/{id}"),
        }
    }

    #[test]
    fn keeps_loads_and_clears_without_showing_secrets() {
        let store = MemoryStore::default();
        assert_eq!(load(&store).unwrap(), None);
        keep(&store, &creds(true)).unwrap();
        let kept = load(&store).unwrap().unwrap();
        assert_eq!(kept, creds(true));
        let debug = format!("{kept:?}");
        assert!(!debug.contains("pw-secret") && !debug.contains("sk-secret"));
        assert!(debug.contains("56789"));
        clear(&store).unwrap();
        assert!(store.names().is_empty());
    }

    #[test]
    fn reads_an_item_of_the_previous_shape() {
        let store = MemoryStore::default();
        store
            .set(
                ITEM,
                br#"{"valkeyUsername":"floppy_d_1","valkeyPassword":"pw","valkeyUrl":"rediss://v:6380","keyScope":"team","s3":null}"#,
            )
            .unwrap();
        assert_eq!(load(&store).unwrap().unwrap().valkey_password, "pw");
        store.set(ITEM, br#"{"valkeyUsername":"x"}"#).unwrap();
        assert!(load(&store).is_err());
    }

    #[test]
    fn secrets_follow_the_engine_contract() {
        let disks = [disk("a"), disk("b")];
        let managed: serde_json::Value =
            serde_json::from_slice(&creds(true).secrets_json(&disks)).unwrap();
        assert_eq!(
            managed,
            serde_json::json!({"version": 1, "meta_password": "pw-secret", "s3": {
                "a": {"access_key": "GK123456789", "secret_key": "sk-secret"},
                "b": {"access_key": "GK123456789", "secret_key": "sk-secret"}}})
        );
        let manual: serde_json::Value =
            serde_json::from_slice(&creds(false).secrets_json(&disks)).unwrap();
        assert_eq!(
            manual,
            serde_json::json!({"version": 1, "meta_password": "pw-secret", "s3": {}})
        );
        assert_eq!(creds(true).s3_tail().as_deref(), Some("56789"));
    }
}
