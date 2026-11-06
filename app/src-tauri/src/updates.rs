//! New releases of the app: once a day the app reads its latest release on
//! GitHub, and the panel offers it. Installing goes through Homebrew, which
//! tracks the cask (DECISIONS #10): a shell detached from the app runs
//! `brew upgrade --cask`, then ends this app and opens the new one. Homebrew
//! does not quit the app itself: it sees it runs inside it ("Skipping quitting
//! application ... as `brew` is running inside it", measured with 0.2.4), and
//! only replaces the bundle. A failure leaves this app running, and the
//! shell's log says why. The engine update the new app brings is offered by
//! the panel as before, and waits for the person.

use serde::Serialize;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};

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
    /// The DMG's size, from the release; None when GitHub did not say.
    pub total: Option<u64>,
    /// While installing: what Homebrew does, and the bytes it has downloaded.
    pub step: Option<InstallStep>,
    pub downloaded: Option<u64>,
}

/// What an installation is doing: the DMG is about 430 MB (the whole engine
/// travels in it), so the download is minutes and the panel shows it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallStep {
    /// `brew update`, before the download.
    Preparing,
    Downloading,
    /// Downloaded: Homebrew replaces the app, which then closes and reopens.
    Installing,
}

/// Where Homebrew downloads: `HOMEBREW_CACHE`, else its default.
fn brew_downloads() -> Option<PathBuf> {
    let cache = std::env::var_os("HOMEBREW_CACHE")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|h| Path::new(&h).join("Library/Caches/Homebrew"))
        })?;
    Some(cache.join("downloads"))
}

/// The step and the bytes so far, from Homebrew's download of this version's
/// DMG (`<hash>--Floppy_<version>_aarch64.dmg`, `.incomplete` while it comes),
/// counting only a file touched since the installation started.
pub fn progress(downloads: &Path, version: &str, since: SystemTime) -> (InstallStep, Option<u64>) {
    let name = format!("--Floppy_{version}_aarch64.dmg");
    let partial = format!("{name}.incomplete");
    let mut found = (InstallStep::Preparing, None);
    let Ok(entries) = std::fs::read_dir(downloads) else {
        return found;
    };
    for e in entries.flatten() {
        let file = e.file_name().to_string_lossy().into_owned();
        let Ok(meta) = e.metadata() else { continue };
        if meta.modified().is_ok_and(|m| m < since) {
            continue;
        }
        if file.ends_with(&partial) {
            found = (InstallStep::Downloading, Some(meta.len()));
        } else if file.ends_with(&name) {
            return (InstallStep::Installing, Some(meta.len()));
        }
    }
    found
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

/// The shell that updates the app, with the brew, the cask, the bundle and
/// this app's pid as arguments: the tap first, so the new cask is seen, then
/// the upgrade; only once it succeeded, this app ends (up to 10 s) and the new
/// bundle opens. `brew update` failing on another tap does not stop it; the
/// upgrade failing leaves this app as it is.
pub fn script() -> &'static str {
    r#""$1" update --quiet || true
HOMEBREW_NO_AUTO_UPDATE=1 "$1" upgrade --cask "$2" || exit $?
kill -TERM "$4" 2>/dev/null
i=0
while kill -0 "$4" 2>/dev/null && [ "$i" -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
/usr/bin/open "$3""#
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
                    Ok((tag, total)) => me.found_sized(&tag, total),
                    // Offline or rate limited: the next day tries again.
                    Err(e) => eprintln!("floppy: could not read the latest release: {e}"),
                }
                thread::sleep(CHECK_EVERY);
            }
        });
    }

    /// Records the latest release's tag, unless an installation is under way.
    #[cfg(test)]
    pub fn found(&self, tag: &str) {
        if self
            .lock()
            .as_ref()
            .is_some_and(|u| u.state == UpdateState::Installing)
        {
            return;
        }
        self.found_sized(tag, None);
    }

    /// Records the latest release's tag and its DMG's size, unless an
    /// installation is under way.
    pub fn found_sized(&self, tag: &str, total: Option<u64>) {
        if self
            .lock()
            .as_ref()
            .is_some_and(|u| u.state == UpdateState::Installing)
        {
            return;
        }
        self.set(newer(tag, &self.current).map(|version| AppUpdate {
            version,
            state: UpdateState::Available,
            error: None,
            total,
            step: None,
            downloaded: None,
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
            .arg(std::process::id().to_string())
            .env(
                "PATH",
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            )
            .env("HOMEBREW_NO_ENV_HINTS", "1")
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            // Its own process group: signals to the app's group spare it.
            .process_group(0)
            .spawn()
            .map_err(|e| e.to_string())?;
        let started = SystemTime::now();
        self.set(Some(AppUpdate {
            state: UpdateState::Installing,
            error: None,
            step: Some(InstallStep::Preparing),
            downloaded: None,
            ..update.clone()
        }));
        // The download's progress, every second, while the installation runs.
        let watcher = Arc::clone(self);
        let version = update.version.clone();
        thread::spawn(move || {
            let Some(dir) = brew_downloads() else { return };
            loop {
                thread::sleep(Duration::from_secs(1));
                let mut cur = watcher.lock();
                let Some(u) = cur.as_mut().filter(|u| u.state == UpdateState::Installing) else {
                    return;
                };
                let (step, downloaded) = progress(&dir, &version, started);
                if u.step != Some(step) || u.downloaded != downloaded {
                    u.step = Some(step);
                    u.downloaded = downloaded;
                    let next = cur.clone();
                    drop(cur);
                    (watcher.emit)(next);
                }
            }
        });
        let me = Arc::clone(self);
        thread::spawn(move || {
            // On success the shell ends this app before it ends itself: still
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
                step: None,
                downloaded: None,
                ..update
            }));
        });
        Ok(())
    }
}

/// The latest release's tag and the size of its DMG.
fn latest_tag(agent: &ureq::Agent) -> Result<(String, Option<u64>), String> {
    #[derive(serde::Deserialize)]
    struct Asset {
        name: String,
        size: u64,
    }
    #[derive(serde::Deserialize)]
    struct Release {
        tag_name: String,
        #[serde(default)]
        assets: Vec<Asset>,
    }
    let release: Release = agent
        .get(RELEASES)
        .header("Accept", "application/vnd.github+json")
        .call()
        .map_err(|e| e.to_string())?
        .body_mut()
        .read_json::<Release>()
        .map_err(|e| e.to_string())?;
    let total = release
        .assets
        .iter()
        .find(|a| a.name.ends_with(".dmg"))
        .map(|a| a.size);
    Ok((release.tag_name, total))
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
    fn the_download_is_read_from_homebrews_cache() {
        let dir = tempfile::tempdir().unwrap();
        let since = SystemTime::now() - Duration::from_secs(5);
        assert_eq!(
            progress(dir.path(), "0.2.8", since),
            (InstallStep::Preparing, None)
        );
        // Another version's DMG, finished long ago, does not count.
        std::fs::write(dir.path().join("aa--Floppy_0.2.6_aarch64.dmg"), b"old").unwrap();
        let partial = dir.path().join("bb--Floppy_0.2.8_aarch64.dmg.incomplete");
        std::fs::write(&partial, vec![0u8; 1500]).unwrap();
        assert_eq!(
            progress(dir.path(), "0.2.8", since),
            (InstallStep::Downloading, Some(1500))
        );
        std::fs::rename(&partial, dir.path().join("bb--Floppy_0.2.8_aarch64.dmg")).unwrap();
        assert_eq!(
            progress(dir.path(), "0.2.8", since),
            (InstallStep::Installing, Some(1500))
        );
        // A file from before this installation is not its download.
        let later = SystemTime::now() + Duration::from_secs(60);
        assert_eq!(
            progress(dir.path(), "0.2.8", later),
            (InstallStep::Preparing, None)
        );
    }

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
        assert_eq!(
            bundle_of(exe),
            Some(PathBuf::from("/Applications/Floppy.app"))
        );
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
            total: None,
            step: None,
            downloaded: None,
        });
        u.found("v0.2.3");
        assert_eq!(u.get().unwrap().state, UpdateState::Installing);
    }

    /// The shell with a fake brew: a successful upgrade ends the old app and
    /// opens the new one, a failed one touches neither, a failed `brew update`
    /// does not stop the upgrade, and the arguments arrive whole (paths with
    /// spaces).
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
        std::fs::set_permissions(&brew, std::os::unix::fs::PermissionsExt::from_mode(0o755))
            .unwrap();
        // A process standing in for the old app: the shell must end it. `open`
        // of a path that does not exist fails: the exit status tells whether
        // the shell got that far.
        // Detached, as the real app is not the shell's child: a child of the
        // test would linger unreaped and look alive.
        let detached = || -> String {
            let out = Command::new("/bin/sh")
                .args(["-c", "/bin/sleep 30 >/dev/null 2>&1 & echo $!"])
                .output()
                .unwrap();
            String::from_utf8(out.stdout).unwrap().trim().to_string()
        };
        let alive = |pid: &str| {
            Command::new("/bin/kill")
                .args(["-0", pid])
                .status()
                .unwrap()
                .success()
        };
        let run = |pid: &str| {
            Command::new("/bin/sh")
                .args(["-c", script(), "sh"])
                .arg(&brew)
                .arg(CASK)
                .arg(dir.path().join("No Such.app"))
                .arg(pid)
                .stderr(Stdio::null())
                .status()
                .unwrap()
                .code()
        };
        let app = detached();
        assert_eq!(run(&app), Some(1), "open ran and failed on the missing app");
        assert!(!alive(&app), "the old app was ended");
        assert_eq!(
            std::fs::read_to_string(&calls).unwrap(),
            "update --quiet\nupgrade --cask essedev/floppy/floppy\n"
        );
        std::fs::write(dir.path().join("fail"), "").unwrap();
        let app = detached();
        assert_eq!(run(&app), Some(3), "a failed upgrade stops before open");
        assert!(alive(&app), "a failed upgrade leaves the app running");
        Command::new("/bin/kill").arg(&app).status().unwrap();
    }
}
