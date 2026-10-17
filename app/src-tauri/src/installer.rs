//! The engine Floppy.app carries in `Contents/Resources/engine` (built by
//! `scripts/build-engine.sh`) and its install on this Mac. The app does not
//! copy anything itself: it runs the bundled `install.sh`, the same script the
//! tester zip runs, which replaces the engine's files in the home, keeps the
//! configuration, the VM and the volume, and runs `floppy setup`. The app only
//! records which engine build it installed, to know when the bundle is newer.
//!
//! No Tauri dependency here, so the module is tested against fake bundles.

use crate::engine::{self, EngineError, HOME_ENV};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::Child;

/// The bundle's folder inside the app's resources.
pub const BUNDLE_DIR: &str = "engine";
/// The engine build last installed by the app, in the engine's home. Gone with
/// the rest of the home on `floppy uninstall`.
const STAMP: &str = "engine-build";

/// The engine in the app's bundle.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bundle {
    pub dir: PathBuf,
    /// The hash of the bundle's files, from its `BUILD` file.
    pub build: String,
}

/// What this Mac needs from the bundle.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallNeed {
    /// No engine in the home yet.
    Fresh,
    /// An engine from another build: the zip, or an older app.
    Update,
}

/// The bundle and the home it installs into.
#[derive(Debug, Clone)]
pub struct Installer {
    pub bundle: Bundle,
    pub home: PathBuf,
}

impl Bundle {
    /// The engine under `resources`, if this build of the app carries one
    /// (`tauri dev` and the tests do not).
    pub fn find(resources: &Path) -> Option<Bundle> {
        let dir = resources.join(BUNDLE_DIR);
        let build = std::fs::read_to_string(dir.join("BUILD")).ok()?;
        let build = build.trim().to_string();
        if build.is_empty() || !dir.join("install.sh").is_file() {
            return None;
        }
        Some(Bundle { dir, build })
    }
}

impl Installer {
    /// What the home needs: nothing when it runs this bundle's engine.
    pub fn need(&self) -> Option<InstallNeed> {
        if !engine::is_executable(&self.home.join("bin/floppy")) {
            return Some(InstallNeed::Fresh);
        }
        let installed = std::fs::read_to_string(self.home.join(STAMP)).unwrap_or_default();
        (installed.trim() != self.bundle.build).then_some(InstallNeed::Update)
    }

    /// Starts the bundled `install.sh` for the home, in its own process group.
    pub fn spawn(&self) -> Result<Child, EngineError> {
        let script = self.bundle.dir.join("install.sh");
        let script = script.to_string_lossy();
        let mut cmd = engine::command(Path::new("/bin/bash"), &[script.as_ref()]);
        cmd.env(HOME_ENV, &self.home);
        engine::spawn_command(cmd)
    }

    /// Records that the home now runs this bundle's engine.
    pub fn record(&self) -> std::io::Result<()> {
        std::fs::write(self.home.join(STAMP), format!("{}\n", self.bundle.build))
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::fs::PermissionsExt;

    /// A fake bundle whose `install.sh` runs `body` (with `$FLOPPY_HOME` set).
    pub fn fake_bundle(dir: &Path, build: &str, body: &str) -> Bundle {
        let engine = dir.join(BUNDLE_DIR);
        std::fs::create_dir_all(&engine).unwrap();
        std::fs::write(engine.join("BUILD"), format!("{build}\n")).unwrap();
        let mut f = std::fs::File::create(engine.join("install.sh")).unwrap();
        writeln!(f, "set -eu\n{body}").unwrap();
        Bundle::find(dir).unwrap()
    }

    /// An install.sh that puts an executable `bin/floppy` running `floppy`.
    pub fn installs(floppy: &str) -> String {
        format!(
            r#"mkdir -p "$FLOPPY_HOME/bin"
cat > "$FLOPPY_HOME/bin/floppy" <<'EOF'
#!/bin/bash
{floppy}
EOF
chmod 755 "$FLOPPY_HOME/bin/floppy""#
        )
    }

    #[test]
    fn a_bundle_needs_its_build_and_script() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(Bundle::find(dir.path()), None);
        let engine = dir.path().join(BUNDLE_DIR);
        std::fs::create_dir_all(&engine).unwrap();
        std::fs::write(engine.join("BUILD"), "abc\n").unwrap();
        assert_eq!(Bundle::find(dir.path()), None);
        std::fs::write(engine.join("install.sh"), "").unwrap();
        assert_eq!(Bundle::find(dir.path()).unwrap().build, "abc");
        std::fs::write(engine.join("BUILD"), "\n").unwrap();
        assert_eq!(Bundle::find(dir.path()), None);
    }

    #[test]
    fn need_follows_the_home() {
        let dir = tempfile::tempdir().unwrap();
        let bundle = fake_bundle(dir.path(), "b2", "");
        let home = dir.path().join("home");
        let inst = Installer {
            bundle,
            home: home.clone(),
        };
        assert_eq!(inst.need(), Some(InstallNeed::Fresh));
        std::fs::create_dir_all(home.join("bin")).unwrap();
        let bin = home.join("bin/floppy");
        std::fs::write(&bin, "").unwrap();
        // Not executable yet: as good as missing.
        assert_eq!(inst.need(), Some(InstallNeed::Fresh));
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
        // An engine from the zip has no stamp.
        assert_eq!(inst.need(), Some(InstallNeed::Update));
        std::fs::write(home.join(STAMP), "b1\n").unwrap();
        assert_eq!(inst.need(), Some(InstallNeed::Update));
        inst.record().unwrap();
        assert_eq!(inst.need(), None);
    }

    #[test]
    fn the_script_runs_with_the_home() {
        let dir = tempfile::tempdir().unwrap();
        let bundle = fake_bundle(
            dir.path(),
            "b1",
            r#"echo "home=$FLOPPY_HOME path=$PATH" >&2"#,
        );
        let home = dir.path().join("home");
        let inst = Installer {
            bundle,
            home: home.clone(),
        };
        let child = inst.spawn().unwrap();
        let (status, tail) = engine::follow(child, |_| {});
        assert!(status.unwrap().success());
        assert_eq!(
            tail,
            [format!(
                "home={} path=/usr/bin:/bin:/usr/sbin:/sbin",
                home.display()
            )]
        );
    }
}
