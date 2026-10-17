//! Where the app keeps what the control plane hands it: the macOS Keychain,
//! never a file, a log or a command line (docs/DATABASE_SCHEMA.md, "Secrets").
//! Each item is a generic password of the app's service, holding JSON.

#[cfg(test)]
use std::collections::HashMap;
#[cfg(test)]
use std::sync::Mutex;

/// The Keychain service of every item: the app's bundle identifier.
pub const SERVICE: &str = "dev.esse.floppy.menubar";

/// `errSecItemNotFound`.
const NOT_FOUND: i32 = -25300;

/// A Keychain failure, with the OSStatus when there is one. Never the value.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("Keychain: {message}")]
pub struct SecretError {
    pub message: String,
}

/// Reads, writes and deletes items by name.
pub trait SecretStore: Send + Sync {
    /// The item, or None when there is none.
    fn get(&self, name: &str) -> Result<Option<Vec<u8>>, SecretError>;
    /// Creates or replaces the item.
    fn set(&self, name: &str, value: &[u8]) -> Result<(), SecretError>;
    /// Deletes the item; deleting a missing one succeeds.
    fn delete(&self, name: &str) -> Result<(), SecretError>;
}

/// The login Keychain, through Security.framework.
pub struct Keychain;

#[cfg(target_os = "macos")]
fn keychain_error(e: security_framework::base::Error) -> SecretError {
    SecretError {
        message: format!("{e} ({})", e.code()),
    }
}

#[cfg(target_os = "macos")]
impl SecretStore for Keychain {
    fn get(&self, name: &str) -> Result<Option<Vec<u8>>, SecretError> {
        use security_framework::passwords::{PasswordOptions, generic_password};
        match generic_password(PasswordOptions::new_generic_password(SERVICE, name)) {
            Ok(v) => Ok(Some(v)),
            Err(e) if e.code() == NOT_FOUND => Ok(None),
            Err(e) => Err(keychain_error(e)),
        }
    }

    fn set(&self, name: &str, value: &[u8]) -> Result<(), SecretError> {
        security_framework::passwords::set_generic_password(SERVICE, name, value)
            .map_err(keychain_error)
    }

    fn delete(&self, name: &str) -> Result<(), SecretError> {
        match security_framework::passwords::delete_generic_password(SERVICE, name) {
            Ok(()) => Ok(()),
            Err(e) if e.code() == NOT_FOUND => Ok(()),
            Err(e) => Err(keychain_error(e)),
        }
    }
}

#[cfg(not(target_os = "macos"))]
impl SecretStore for Keychain {
    fn get(&self, _: &str) -> Result<Option<Vec<u8>>, SecretError> {
        Err(SecretError {
            message: "no Keychain on this system".into(),
        })
    }
    fn set(&self, name: &str, _: &[u8]) -> Result<(), SecretError> {
        self.get(name).map(|_| ())
    }
    fn delete(&self, name: &str) -> Result<(), SecretError> {
        self.get(name).map(|_| ())
    }
}

/// A store in memory, for tests; `failing` makes every call fail like a
/// Keychain the user refused.
#[cfg(test)]
#[derive(Default)]
pub struct MemoryStore {
    items: Mutex<HashMap<String, Vec<u8>>>,
    pub failing: std::sync::atomic::AtomicBool,
}

#[cfg(test)]
impl MemoryStore {
    fn check(&self) -> Result<(), SecretError> {
        if self.failing.load(std::sync::atomic::Ordering::SeqCst) {
            return Err(SecretError {
                message: "refused (-128)".into(),
            });
        }
        Ok(())
    }

    /// The names of the items held, sorted.
    pub fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = self
            .items
            .lock()
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default();
        names.sort();
        names
    }
}

#[cfg(test)]
impl SecretStore for MemoryStore {
    fn get(&self, name: &str) -> Result<Option<Vec<u8>>, SecretError> {
        self.check()?;
        Ok(self.items.lock().ok().and_then(|m| m.get(name).cloned()))
    }
    fn set(&self, name: &str, value: &[u8]) -> Result<(), SecretError> {
        self.check()?;
        if let Ok(mut m) = self.items.lock() {
            m.insert(name.to_string(), value.to_vec());
        }
        Ok(())
    }
    fn delete(&self, name: &str) -> Result<(), SecretError> {
        self.check()?;
        if let Ok(mut m) = self.items.lock() {
            m.remove(name);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Writes, reads and deletes a throwaway item in the real login Keychain.
    /// Ignored by default: it touches the user's Keychain and may prompt.
    #[test]
    #[ignore]
    fn round_trips_in_the_real_keychain() {
        let name = "test-round-trip";
        Keychain.delete(name).unwrap();
        assert_eq!(Keychain.get(name).unwrap(), None);
        Keychain.set(name, b"one").unwrap();
        Keychain.set(name, b"two").unwrap();
        assert_eq!(Keychain.get(name).unwrap().as_deref(), Some(&b"two"[..]));
        Keychain.delete(name).unwrap();
        Keychain.delete(name).unwrap();
        assert_eq!(Keychain.get(name).unwrap(), None);
    }

    #[test]
    fn memory_store_behaves_like_the_keychain() {
        let s = MemoryStore::default();
        assert_eq!(s.get("a").unwrap(), None);
        s.set("a", b"x").unwrap();
        assert_eq!(s.get("a").unwrap().as_deref(), Some(&b"x"[..]));
        s.delete("a").unwrap();
        s.delete("a").unwrap();
        assert!(s.names().is_empty());
        s.failing.store(true, std::sync::atomic::Ordering::SeqCst);
        assert!(s.get("a").is_err());
    }
}
