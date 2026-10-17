//! The app's own preferences, a small JSON file in its config folder
//! (`~/Library/Application Support/dev.esse.floppy.menubar/prefs.json`).
//! The disk's configuration stays in the engine's `~/.floppy/config.env`.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// The preferences the app keeps between launches.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Prefs {
    /// The first-run window was completed or closed for good.
    pub first_run_done: bool,
}

/// Reads and writes `Prefs` at one path.
pub struct PrefsStore {
    path: PathBuf,
}

impl PrefsStore {
    /// The store for `prefs.json` in `dir`.
    pub fn new(dir: &Path) -> Self {
        PrefsStore {
            path: dir.join("prefs.json"),
        }
    }

    /// The stored preferences; defaults when the file is missing or unreadable.
    pub fn load(&self) -> Prefs {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    /// Writes through a temporary file, so a crash never leaves half a file.
    pub fn save(&self, prefs: &Prefs) -> std::io::Result<()> {
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
        store
            .save(&Prefs {
                first_run_done: true,
            })
            .unwrap();
        assert!(store.load().first_run_done);
        std::fs::write(dir.path().join("nested/prefs.json"), "not json").unwrap();
        assert_eq!(store.load(), Prefs::default());
    }
}
