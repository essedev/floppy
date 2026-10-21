//! New releases of the app: once a day the app reads its latest release on
//! GitHub, and the panel offers it. Installing goes through Homebrew, which
//! tracks the cask (DECISIONS #10): a shell detached from the app runs
//! `brew upgrade --cask`, which quits this app before replacing it (the cask's
//! `uninstall quit`), then opens the new one. A failure leaves this app
//! running, and the shell's log says why. The engine update the new app
//! brings is offered by the panel as before, and waits for the person.

use serde::Serialize;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

/// The app's latest published release (drafts and prereleases excluded).
pub const RELEASES: &str = "https://api.github.com/repos/essedev/floppy/releases/latest";
/// The cask, fully qualified as `brew install --cask` takes it.
pub const CASK: &str = "essedev/floppy/floppy";
/// Where Homebrew lives on Apple silicon, then on Intel.
const BREWS: [&str; 2] = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"];

const FIRST_CHECK: Duration = Duration::from_secs(20);
const CHECK_EVERY: Duration = Duration::from_secs(24 * 60 * 60);
const TIMEOUT: Duration = Duration::from_secs(15);

/// A newer release, and where its installation stands.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AppUpdate {
    pub version: String,
    pub state: UpdateState,
    /// The last line Homebrew printed when it failed.
    pub error: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpdateState {
    Available,
    Installing,
    Failed,
}

/// `X.Y.Z` from a tag like `v0.2.1`; anything else is not a release.
pub fn parse_version(tag: &str) -> Option<(u64, u64, u64)> {
    let mut parts = tag.strip_prefix('v').unwrap_or(tag).split('.');
    let mut next = || parts.next()?.parse::<u64>().ok();
    let v = (next()?, next()?, next()?);
    parts.next().is_none().then_some(v)
}

/// The release's version when it is newer than the running one.
pub fn newer(tag: &str, current: &str) -> Option<String> {
    let latest = parse_version(tag)?;
    let now = parse_version(current)?;
    (latest > now).then(|| format!("{}.{}.{}", latest.0, latest.1, latest.2))
}

/// The `.app` this process runs from: `<bundle>/Contents/MacOS/<binary>`.
fn bundle_of(exe: &Path) -> Option<PathBuf> {
    let bundle = exe.parent()?.parent()?.parent()?;
    (bundle.extension()? == "app").then(|| bundle.to_path_buf())
}

/// The shell that updates the app: the tap first, so the new cask is seen,
/// then the upgrade, then the new app. `brew update` failing on another tap
/// does not stop it; the upgrade failing leaves this app as it is.
pub fn script() -> &'static str {
    r#""$1" update --quiet || true
HOMEBREW_NO_AUTO_UPDATE=1 "$1" upgrade --cask "$2" && /usr/bin/open "$3""#
}

/// Checks for and installs new releases.
pub struct Updates {
    current: String,
    log: PathBuf,
    state: Mutex<Option<AppUpdate>>,
    emit: Box<dyn Fn(Option<AppUpdate>) + Send + Sync>,
}

impl Updates {
    pub fn new(
        current: String,
        log: PathBuf,
        emit: impl Fn(Option<AppUpdate>) + Send + Sync + 'static,
    ) -> Arc<Self> {
        Arc::new(Updates {
            current,
            log,
            state: Mutex::new(None),
            emit: Box::new(emit),
        })
    }

    /// The newer release, if one was found.
    pub fn get(&self) -> Option<AppUpdate> {
        self.lock().clone()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Option<AppUpdate>> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn set(&self, next: Option<AppUpdate>) {
        let mut cur = self.lock();
        if *cur != next {
            *cur = next.clone();
            drop(cur);
            (self.emit)(next);
        }
    }

    /// Starts the daily check. Call once.
    pub fn start(self: &Arc<Self>, user_agent: String) {
        let me = Arc::clone(self);
        thread::spawn(move || {
            let tls = ureq::tls::TlsConfig::builder()
                .root_certs(ureq::tls::RootCerts::PlatformVerifier)
                .build();
            let agent = ureq::Agent::config_builder()
                .timeout_global(Some(TIMEOUT))
                .user_agent(&user_agent)
                .tls_config(tls)
                .build()
                .new_agent();
            thread::sleep(FIRST_CHECK);
            loop {
                match latest_tag(&agent) {
                    Ok(tag) => me.found(&tag),
                    // Offline or rate limited: the next day tries again.
                    Err(e) => eprintln!("floppy: could not read the latest release: {e}"),
                }
                thread::sleep(CHECK_EVERY);
            }
        });
    }

    /// Records the latest release's tag, unless an installation is under way.
    pub fn found(&self, tag: &str) {
        if self.lock().as_ref().is_some_and(|u| u.state == UpdateState::Installing) {
            return;
        }
        self.set(newer(tag, &self.current).map(|version| AppUpdate {
            version,
            state: UpdateState::Available,
            error: None,
        }));
    }

    /// Runs the upgrade in a shell of its own: this app is quit by Homebrew
    /// halfway, and the shell must live on to open the new one.
    pub fn install(self: &Arc<Self>) -> Result<(), String> {
        let Some(update) = self.get() else {
            return Err("no newer release".into());
        };
        if update.state == UpdateState::Installing {
            return Ok(());
        }
        let brew = BREWS
            .iter()
            .find(|b| Path::new(b).exists())
            .ok_or("Homebrew is not installed")?;
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let bundle = bundle_of(&exe).ok_or("the app does not run from a bundle")?;
        if let Some(dir) = self.log.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let out = File::create(&self.log).map_err(|e| e.to_string())?;
        let err = out.try_clone().map_err(|e| e.to_string())?;
        let mut child = Command::new("/bin/sh")
            .args(["-c", script(), "sh", brew, CASK])
            .arg(&bundle)
            .env("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
            .env("HOMEBREW_NO_ENV_HINTS", "1")
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            // Its own process group: signals to the app's group spare it.
            .process_group(0)
            .spawn()
            .map_err(|e| e.to_string())?;
        self.set(Some(AppUpdate {
            state: UpdateState::Installing,
            error: None,
            ..update.clone()
        }));
        let me = Arc::clone(self);
        thread::spawn(move || {
            // On success Homebrew quits this app before the shell ends: still
            // being here when it ends means the app was not replaced.
            let ok = child.wait().is_ok_and(|s| s.success());
            let error = if ok {
                "Homebrew did not replace the app".to_string()
            } else {
                last_line(&me.log).unwrap_or_else(|| "Homebrew failed".into())
            };
            me.set(Some(AppUpdate {
                state: UpdateState::Failed,
                error: Some(error),
                ..update
            }));
        });
        Ok(())
    }
}

fn latest_tag(agent: &ureq::Agent) -> Result<String, String> {
    #[derive(serde::Deserialize)]
    struct Release {
        tag_name: String,
    }
    let release: Release = agent
        .get(RELEASES)
        .header("Accept", "application/vnd.github+json")
        .call()
        .map_err(|e| e.to_string())?
        .body_mut()
        .read_json()
        .map_err(|e| e.to_string())?;
    Ok(release.tag_name)
}

fn last_line(path: &Path) -> Option<String> {
    let file = File::open(path).ok()?;
    BufReader::new(file)
        .lines()
        .map_while(Result::ok)
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .last()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_are_plain_triples() {
        assert_eq!(parse_version("v0.2.1"), Some((0, 2, 1)));
        assert_eq!(parse_version("1.10.0"), Some((1, 10, 0)));
        assert_eq!(parse_version("v0.2"), None);
        assert_eq!(parse_version("v0.2.1-beta"), None);
        assert_eq!(parse_version("v0.2.1.4"), None);
    }

    #[test]
    fn only_a_newer_release_counts() {
        assert_eq!(newer("v0.2.2", "0.2.1").as_deref(), Some("0.2.2"));
        assert_eq!(newer("v0.10.0", "0.9.9").as_deref(), Some("0.10.0"));
        assert_eq!(newer("v0.2.1", "0.2.1"), None);
        assert_eq!(newer("v0.2.0", "0.2.1"), None);
        assert_eq!(newer("nightly", "0.2.1"), None);
    }

    #[test]
    fn the_bundle_is_three_levels_up() {
        let exe = Path::new("/Applications/Floppy.app/Contents/MacOS/floppy-app");
        assert_eq!(bundle_of(exe), Some(PathBuf::from("/Applications/Floppy.app")));
        assert_eq!(bundle_of(Path::new("/usr/local/bin/floppy-app")), None);
    }

    fn updates(seen: Arc<Mutex<Vec<Option<AppUpdate>>>>) -> Arc<Updates> {
        Updates::new("0.2.1".into(), PathBuf::from("/nonexistent"), move |u| {
            seen.lock().unwrap().push(u);
        })
    }

    #[test]
    fn a_newer_release_is_offered_once() {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let u = updates(Arc::clone(&seen));
        u.found("v0.2.1");
        assert_eq!(u.get(), None);
        u.found("v0.2.2");
        u.found("v0.2.2");
        assert_eq!(u.get().map(|x| x.version), Some("0.2.2".into()));
        assert_eq!(seen.lock().unwrap().len(), 1);
    }

    #[test]
    fn a_check_does_not_undo_an_installation() {
        let seen = Arc::new(Mutex::new(Vec::new()));
        let u = updates(seen);
        u.found("v0.2.2");
        *u.lock() = Some(AppUpdate {
            version: "0.2.2".into(),
            state: UpdateState::Installing,
            error: None,
        });
        u.found("v0.2.3");
        assert_eq!(u.get().unwrap().state, UpdateState::Installing);
    }

    /// The shell with a fake brew: a failed upgrade does not open the app, a
    /// failed `brew update` does not stop the upgrade, and the arguments
    /// arrive whole (paths with spaces).
    #[test]
    fn the_script_runs_update_then_upgrade_then_open() {
        let dir = tempfile::tempdir().unwrap();
        let brew = dir.path().join("my brew");
        let calls = dir.path().join("calls");
        std::fs::write(
            &brew,
            format!(
                "#!/bin/sh\necho \"$*\" >> '{}'\n[ \"$1\" = update ] && exit 1\n[ -f '{}/fail' ] && exit 3\nexit 0\n",
                calls.display(),
                dir.path().display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&brew, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        // `open` of a path that does not exist fails: the exit status tells
        // whether the shell got that far.
        let run = || {
            Command::new("/bin/sh")
                .args(["-c", script(), "sh"])
                .arg(&brew)
                .arg(CASK)
                .arg(dir.path().join("No Such.app"))
                .stderr(Stdio::null())
                .status()
                .unwrap()
                .code()
        };
        assert_eq!(run(), Some(1), "open ran and failed on the missing app");
        assert_eq!(
            std::fs::read_to_string(&calls).unwrap(),
            "update --quiet\nupgrade --cask essedev/floppy/floppy\n"
        );
        std::fs::write(dir.path().join("fail"), "").unwrap();
        assert_eq!(run(), Some(3), "a failed upgrade stops before open");
    }
}
