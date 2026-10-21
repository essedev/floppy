//! Where the app keeps what the control plane hands it: the macOS Keychain,
//! never a file, a log or a command line (docs/DATABASE_SCHEMA.md, "Secrets").
//!
//! The named items (the device, the disks' credentials) live together in one
//! generic password of the app's service, `secrets`, read once per launch.
//! Without a Developer ID macOS admits each build of the app to an item by
//! its own code hash, so every update asks the Mac's password once per item
//! read: one item, one question. Installs from before keep an item per name,
//! moved into `secrets` on the first read and then deleted.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as B64;
use std::collections::BTreeMap;
#[cfg(test)]
use std::collections::HashMap;
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

/// The single item holding every named one.
pub const BUNDLE: &str = "secrets";
/// The items of installs from before the bundle, moved into it.
const LEGACY: [&str; 2] = ["device", "disk-credentials"];

/// The login Keychain: every name in the one `secrets` item.
pub type Keychain = Bundled<RawKeychain>;

impl Keychain {
    pub fn new() -> Self {
        Bundled::over(RawKeychain)
    }
}

/// Named items kept as one item of `inner`, read once and cached; the values
/// are base64 in a JSON object.
pub struct Bundled<S> {
    inner: S,
    cache: Mutex<Option<BTreeMap<String, String>>>,
}

impl<S: SecretStore> Bundled<S> {
    pub fn over(inner: S) -> Self {
        Bundled {
            inner,
            cache: Mutex::new(None),
        }
    }

    fn corrupt(what: &str) -> SecretError {
        SecretError {
            message: format!("the {BUNDLE} item is not readable: {what}"),
        }
    }

    /// The items, from the cache or from the Keychain, moving the legacy
    /// ones in when there is no bundle yet.
    fn with_items<T>(
        &self,
        f: impl FnOnce(&mut BTreeMap<String, String>) -> Result<(T, bool), SecretError>,
    ) -> Result<T, SecretError> {
        let mut cache = self.cache.lock().unwrap_or_else(|e| e.into_inner());
        if cache.is_none() {
            *cache = Some(self.load()?);
        }
        let items = cache.as_mut().expect("loaded above");
        let mut next = items.clone();
        let (out, changed) = f(&mut next)?;
        if changed {
            self.save(&next)?;
            *items = next;
        }
        Ok(out)
    }

    fn load(&self) -> Result<BTreeMap<String, String>, SecretError> {
        if let Some(raw) = self.inner.get(BUNDLE)? {
            return serde_json::from_slice(&raw).map_err(|e| Self::corrupt(&e.to_string()));
        }
        let mut items = BTreeMap::new();
        for name in LEGACY {
            if let Some(v) = self.inner.get(name)? {
                items.insert(name.to_string(), B64.encode(v));
            }
        }
        if !items.is_empty() {
            // Written before the old items go: a failure in between leaves
            // them readable, never lost.
            self.save(&items)?;
            for name in LEGACY {
                self.inner.delete(name)?;
            }
        }
        Ok(items)
    }

    fn save(&self, items: &BTreeMap<String, String>) -> Result<(), SecretError> {
        if items.is_empty() {
            return self.inner.delete(BUNDLE);
        }
        let json = serde_json::to_vec(items).map_err(|e| Self::corrupt(&e.to_string()))?;
        self.inner.set(BUNDLE, &json)
    }
}

impl<S: SecretStore> SecretStore for Bundled<S> {
    fn get(&self, name: &str) -> Result<Option<Vec<u8>>, SecretError> {
        self.with_items(|items| {
            let value = items
                .get(name)
                .map(|v| B64.decode(v).map_err(|e| Self::corrupt(&e.to_string())))
                .transpose()?;
            Ok((value, false))
        })
    }

    fn set(&self, name: &str, value: &[u8]) -> Result<(), SecretError> {
        self.with_items(|items| {
            let v = B64.encode(value);
            let changed = items.get(name) != Some(&v);
            items.insert(name.to_string(), v);
            Ok(((), changed))
        })
    }

    fn delete(&self, name: &str) -> Result<(), SecretError> {
        self.with_items(|items| Ok(((), items.remove(name).is_some())))
    }
}

/// One Keychain generic password per name, through Security.framework.
pub struct RawKeychain;

#[cfg(target_os = "macos")]
fn keychain_error(e: security_framework::base::Error) -> SecretError {
    SecretError {
        message: format!("{e} ({})", e.code()),
    }
}

#[cfg(target_os = "macos")]
impl SecretStore for RawKeychain {
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
impl SecretStore for RawKeychain {
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
        RawKeychain.delete(name).unwrap();
        assert_eq!(RawKeychain.get(name).unwrap(), None);
        RawKeychain.set(name, b"one").unwrap();
        RawKeychain.set(name, b"two").unwrap();
        assert_eq!(RawKeychain.get(name).unwrap().as_deref(), Some(&b"two"[..]));
        RawKeychain.delete(name).unwrap();
        RawKeychain.delete(name).unwrap();
        assert_eq!(RawKeychain.get(name).unwrap(), None);
    }

    /// Counts the reads that reach the store underneath.
    #[derive(Default)]
    struct Counting {
        store: MemoryStore,
        reads: std::sync::atomic::AtomicUsize,
    }

    impl SecretStore for Counting {
        fn get(&self, name: &str) -> Result<Option<Vec<u8>>, SecretError> {
            self.reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.store.get(name)
        }
        fn set(&self, name: &str, value: &[u8]) -> Result<(), SecretError> {
            self.store.set(name, value)
        }
        fn delete(&self, name: &str) -> Result<(), SecretError> {
            self.store.delete(name)
        }
    }

    #[test]
    fn every_name_lives_in_one_item_read_once() {
        let b = Bundled::over(Counting::default());
        assert_eq!(b.get("device").unwrap(), None);
        b.set("device", b"{\"a\":1}").unwrap();
        b.set("disk-credentials", &[0, 159, 255]).unwrap();
        assert_eq!(b.get("device").unwrap().as_deref(), Some(&b"{\"a\":1}"[..]));
        assert_eq!(b.get("disk-credentials").unwrap().as_deref(), Some(&[0, 159, 255][..]));
        assert_eq!(b.inner.store.names(), vec![BUNDLE.to_string()]);
        // A new launch reads the one item once, whatever it is asked.
        let again = Bundled::over(Counting::default());
        again.inner.store.set(BUNDLE, &b.inner.store.get(BUNDLE).unwrap().unwrap()).unwrap();
        again.get("device").unwrap();
        again.get("disk-credentials").unwrap();
        assert_eq!(again.inner.reads.load(std::sync::atomic::Ordering::SeqCst), 1);
        // The last name gone, the item goes too.
        b.delete("device").unwrap();
        b.delete("disk-credentials").unwrap();
        assert!(b.inner.store.names().is_empty());
    }

    #[test]
    fn the_items_of_an_older_install_move_into_the_bundle() {
        let old = MemoryStore::default();
        old.set("device", b"dev").unwrap();
        old.set("disk-credentials", b"creds").unwrap();
        let b = Bundled::over(old);
        assert_eq!(b.get("disk-credentials").unwrap().as_deref(), Some(&b"creds"[..]));
        assert_eq!(b.get("device").unwrap().as_deref(), Some(&b"dev"[..]));
        assert_eq!(b.inner.names(), vec![BUNDLE.to_string()]);
    }

    #[test]
    fn a_refused_keychain_stays_an_error_and_loses_nothing() {
        let old = MemoryStore::default();
        old.set("device", b"dev").unwrap();
        old.failing.store(true, std::sync::atomic::Ordering::SeqCst);
        let b = Bundled::over(old);
        assert!(b.get("device").is_err());
        b.inner.failing.store(false, std::sync::atomic::Ordering::SeqCst);
        assert_eq!(b.get("device").unwrap().as_deref(), Some(&b"dev"[..]));
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
