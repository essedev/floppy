//! The app's own preferences, a small JSON file in its config folder
//! (`~/Library/Application Support/dev.esse.floppy.menubar/prefs.json`).
//! The disk's configuration stays in the engine's `~/.floppy/config.env` (or
//! `disks.json` for the team, see `team`); nothing secret goes here (the Keychain holds that, see `account::secrets`).

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// The preferences the app keeps between launches.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Prefs {
    /// The first-run window was completed or closed for good.
    pub first_run_done: bool,
    /// The control plane typed in Settings; None means the build's default.
    pub server_url: Option<String>,
    /// The team disks of a signed-out Mac are still to be cleared off it
    /// (`floppy disks clear` failed or was interrupted): retried.
    pub disks_clear_owed: bool,
}

/// Reads and writes `Prefs` at one path; clones share the write lock.
#[derive(Clone)]
pub struct PrefsStore {
    path: PathBuf,
    writing: Arc<Mutex<()>>,
}

impl PrefsStore {
    /// The store for `prefs.json` in `dir`.
    pub fn new(dir: &Path) -> Self {
        PrefsStore {
            path: dir.join("prefs.json"),
            writing: Arc::new(Mutex::new(())),
        }
    }

    /// The stored preferences; defaults when the file is missing or unreadable.
    pub fn load(&self) -> Prefs {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    /// Changes some preferences and keeps the others.
    pub fn update(&self, change: impl FnOnce(&mut Prefs)) -> std::io::Result<()> {
        let _guard = self.writing.lock().unwrap_or_else(|e| e.into_inner());
        let mut prefs = self.load();
        change(&mut prefs);
        self.save(&prefs)
    }

    /// Writes through a temporary file, so a crash never leaves half a file.
    fn save(&self, prefs: &Prefs) -> std::io::Result<()> {
        if let Some(dir) = self.path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = self.path.with_extension("json.tmp");
        let json = serde_json::to_string_pretty(prefs).map_err(std::io::Error::other)?;
        std::fs::write(&tmp, json)?;
        std::fs::rename(&tmp, &self.path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_and_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let store = PrefsStore::new(&dir.path().join("nested"));
        assert_eq!(store.load(), Prefs::default());
        store.update(|p| p.first_run_done = true).unwrap();
        assert!(store.load().first_run_done);
        std::fs::write(dir.path().join("nested/prefs.json"), "not json").unwrap();
        assert_eq!(store.load(), Prefs::default());
    }

    #[test]
    fn an_update_keeps_the_other_fields() {
        let dir = tempfile::tempdir().unwrap();
        let store = PrefsStore::new(dir.path());
        store
            .update(|p| p.server_url = Some("https://x.it".into()))
            .unwrap();
        store.clone().update(|p| p.first_run_done = true).unwrap();
        let prefs = store.load();
        assert!(prefs.first_run_done);
        assert_eq!(prefs.server_url.as_deref(), Some("https://x.it"));
    }
}
