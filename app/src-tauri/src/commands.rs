//! Tauri commands: thin wrappers over `Core` and the windows. The frontend
//! calls them through `src/lib/backend.ts`.

use crate::account::{Account, AuthView, Refused};
use crate::activity::{Activity, ActivitySnapshot, DeletedItem, FeedPage};
use crate::core::{Core, CoreError, DiskStatus, Folder, RestoreView, SetupProgress};
use crate::engine::JobKind;
use crate::engine::RenameBackRefusal;
use crate::engine::TrashReport;
use crate::prefs::PrefsStore;
use crate::updates::{AppUpdate, Updates};
use crate::windows;
use serde::Serialize;
use std::process::Command;
use std::sync::Arc;
use tauri::{AppHandle, Emitter, State};

/// An error the frontend renders from its dictionary by `code`.
#[derive(Debug, Serialize)]
pub struct CommandError {
    pub code: &'static str,
    pub message: String,
}

impl From<CoreError> for CommandError {
    fn from(e: CoreError) -> Self {
        let code = match &e {
            CoreError::Busy => "busy",
            CoreError::NotMounted => "not_mounted",
            CoreError::NotOnDisk(_) => "not_on_disk",
            CoreError::NothingToCancel => "nothing_to_cancel",
            CoreError::ListTimeout => "list_timeout",
            CoreError::NoBundle => "no_bundle",
            CoreError::NotTeam => "not_team",
            CoreError::NoSuchDisk => "no_such_disk",
            CoreError::CacheSize => "cache_size",
            CoreError::NotInTrash => "not_in_trash",
            CoreError::NoSuchNotice => "no_such_notice",
            CoreError::Server(_) => "server",
            CoreError::Engine(_) => "engine",
        };
        CommandError {
            code,
            message: e.to_string(),
        }
    }
}

impl From<Refused> for CommandError {
    fn from(e: Refused) -> Self {
        CommandError {
            code: match e {
                Refused::Busy => "auth_busy",
                Refused::State => "auth_state",
            },
            message: e.to_string(),
        }
    }
}

fn io_error(e: impl std::fmt::Display) -> CommandError {
    CommandError {
        code: "io",
        message: e.to_string(),
    }
}

type CoreState<'a> = State<'a, Arc<Core>>;

#[tauri::command]
pub fn get_status(core: CoreState) -> DiskStatus {
    core.status()
}

#[tauri::command]
pub fn refresh_status(core: CoreState) {
    core.wake();
}

#[tauri::command]
pub fn start_disk(core: CoreState) -> Result<(), CommandError> {
    Ok(core.run_job(JobKind::Start, None)?)
}

#[tauri::command]
pub fn stop_disk(core: CoreState) -> Result<(), CommandError> {
    Ok(core.run_job(JobKind::Stop, None)?)
}

#[tauri::command]
pub fn warmup(core: CoreState, path: String) -> Result<(), CommandError> {
    let path = core.disk_path(&path)?;
    Ok(core.run_job(JobKind::Warmup, Some(path))?)
}

#[tauri::command]
pub fn free(core: CoreState, path: String) -> Result<(), CommandError> {
    let path = core.disk_path(&path)?;
    Ok(core.run_job(JobKind::Free, Some(path))?)
}

#[tauri::command]
pub fn cancel_job(core: CoreState) -> Result<(), CommandError> {
    Ok(core.cancel()?)
}

#[tauri::command]
pub fn dismiss_error(core: CoreState) {
    core.dismiss_error();
}

/// Restarts Floppy (stop, then start) so the VM takes the memory its disks want.
#[tauri::command]
pub fn restart_disk(core: CoreState) -> Result<(), CommandError> {
    Ok(core.run_job(JobKind::Restart, None)?)
}

/// Turns a team disk on or off on this Mac.
#[tauri::command]
pub fn set_disk_enabled(core: CoreState, id: String, enabled: bool) -> Result<(), CommandError> {
    Ok(core.set_disk_enabled(&id, enabled)?)
}

/// Sets the space for copies of files on this Mac, in MB.
#[tauri::command]
pub fn set_cache_size(core: CoreState, mb: u64) -> Result<(), CommandError> {
    Ok(core.set_cache_size(mb)?)
}

/// The folders at the top of the disk, or of `path` on it.
#[tauri::command]
pub async fn list_folders(
    core: CoreState<'_>,
    path: Option<String>,
) -> Result<Vec<Folder>, CommandError> {
    let core = Arc::clone(&core);
    tauri::async_runtime::spawn_blocking(move || core.folders(path.as_deref()))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

type ActivityState<'a> = State<'a, Arc<Activity>>;

/// Every mounted disk's trash, with who deleted what when the control plane knows.
#[tauri::command]
pub async fn get_trash(
    core: CoreState<'_>,
    activity: ActivityState<'_>,
) -> Result<TrashReport, CommandError> {
    let (core, activity) = (Arc::clone(&core), Arc::clone(&activity));
    tauri::async_runtime::spawn_blocking(move || {
        let mut report = core.trash()?;
        activity.who(&mut report);
        Ok(report)
    })
    .await
    .map_err(io_error)?
}

/// A page of a disk's activity; `disk` is null for the single-user volume.
#[tauri::command]
pub async fn get_activity(
    activity: ActivityState<'_>,
    disk: Option<String>,
    before: Option<String>,
) -> Result<FeedPage, CommandError> {
    let activity = Arc::clone(&activity);
    tauri::async_runtime::spawn_blocking(move || activity.feed(disk.as_deref(), before.as_deref()))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

/// The panel's notices and what is being written now.
#[tauri::command]
pub fn get_activity_snapshot(activity: ActivityState) -> ActivitySnapshot {
    activity.snapshot()
}

/// Puts back what an event deleted, from the trash.
#[tauri::command]
pub async fn restore_deleted(
    activity: ActivityState<'_>,
    disk: Option<String>,
    items: Vec<DeletedItem>,
) -> Result<RestoreView, CommandError> {
    let activity = Arc::clone(&activity);
    tauri::async_runtime::spawn_blocking(move || activity.restore_deleted(disk.as_deref(), &items))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

/// Undoes a rename from the feed; the refusal when the old name is taken or
/// the folder is gone.
#[tauri::command]
pub async fn rename_back(
    activity: ActivityState<'_>,
    disk: Option<String>,
    path: String,
    to: String,
) -> Result<Option<RenameBackRefusal>, CommandError> {
    let activity = Arc::clone(&activity);
    tauri::async_runtime::spawn_blocking(move || activity.rename_back(disk.as_deref(), &path, &to))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

/// Runs a notice's undo.
#[tauri::command]
pub async fn undo_notice(activity: ActivityState<'_>, id: u64) -> Result<(), CommandError> {
    let activity = Arc::clone(&activity);
    tauri::async_runtime::spawn_blocking(move || activity.undo(id))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

#[tauri::command]
pub fn dismiss_notice(activity: ActivityState, id: u64) {
    activity.dismiss(id);
}

/// Puts trash entries back; `disk` is null for the single-user volume.
#[tauri::command]
pub async fn restore_trash(
    core: CoreState<'_>,
    disk: Option<String>,
    ids: Vec<String>,
) -> Result<RestoreView, CommandError> {
    let core = Arc::clone(&core);
    tauri::async_runtime::spawn_blocking(move || core.restore(disk.as_deref(), &ids))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

/// Shows a file in the Finder, selected (`open -R`); only on the disk.
#[tauri::command]
pub fn reveal_in_finder(core: CoreState, path: String) -> Result<(), CommandError> {
    let target = core.disk_path(&path)?;
    Command::new("/usr/bin/open")
        .args(["-R", &target])
        .spawn()
        .map(|_| ())
        .map_err(io_error)
}

/// Opens the disk, or a folder on it, in the Finder.
#[tauri::command]
pub fn open_in_finder(core: CoreState, path: Option<String>) -> Result<(), CommandError> {
    let target = match path {
        Some(p) => core.disk_path(&p)?,
        None => core.status().mount_point.ok_or(CoreError::NotMounted)?,
    };
    Command::new("/usr/bin/open")
        .arg(&target)
        .spawn()
        .map(|_| ())
        .map_err(io_error)
}

#[tauri::command]
pub fn get_setup(core: CoreState) -> SetupProgress {
    core.setup()
}

#[tauri::command]
pub fn run_setup(core: CoreState) -> Result<(), CommandError> {
    Ok(core.run_job(JobKind::Setup, None)?)
}

/// Installs or updates the engine from the app's bundle, then sets it up.
#[tauri::command]
pub fn install_engine(core: CoreState) -> Result<(), CommandError> {
    Ok(core.run_job(JobKind::Install, None)?)
}

/// Facts about this app and this Mac for Settings and the first run.
#[derive(Debug, Serialize)]
pub struct AppInfo {
    pub version: String,
    pub engine_path: String,
    /// The Mac's name in Sharing settings: the default device name.
    pub computer_name: Option<String>,
}

/// The Mac's name (System Settings, General, Sharing).
pub fn computer_name() -> Option<String> {
    Command::new("/usr/sbin/scutil")
        .args(["--get", "ComputerName"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
}

#[tauri::command]
pub async fn app_info(app: AppHandle, core: CoreState<'_>) -> Result<AppInfo, CommandError> {
    let computer_name = tauri::async_runtime::spawn_blocking(computer_name)
        .await
        .map_err(io_error)?;
    Ok(AppInfo {
        version: app.package_info().version.to_string(),
        engine_path: core.engine().display().to_string(),
        computer_name,
    })
}

/// A newer release of the app, if the daily check found one.
#[tauri::command]
pub fn get_app_update(updates: State<'_, Arc<Updates>>) -> Option<AppUpdate> {
    updates.get()
}

/// Upgrades the app through Homebrew; the app quits and the new one opens.
#[tauri::command]
pub fn install_app_update(updates: State<'_, Arc<Updates>>) -> Result<(), CommandError> {
    updates.install().map_err(|message| CommandError {
        code: "update",
        message,
    })
}

type AccountState<'a> = State<'a, Arc<Account>>;

/// Runs an account step off the main thread: each may wait on the network.
async fn account_step(
    account: AccountState<'_>,
    step: impl FnOnce(&Arc<Account>) -> Result<AuthView, Refused> + Send + 'static,
) -> Result<AuthView, CommandError> {
    let account = Arc::clone(&account);
    tauri::async_runtime::spawn_blocking(move || step(&account))
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
}

#[tauri::command]
pub fn auth_view(account: AccountState) -> AuthView {
    account.view()
}

#[tauri::command]
pub async fn auth_start(
    account: AccountState<'_>,
    email: String,
    device_name: String,
) -> Result<AuthView, CommandError> {
    account_step(account, move |a| a.start(&email, &device_name, false)).await
}

#[tauri::command]
pub async fn auth_resend(account: AccountState<'_>) -> Result<AuthView, CommandError> {
    account_step(account, |a| a.resend()).await
}

#[tauri::command]
pub async fn auth_back(account: AccountState<'_>) -> Result<AuthView, CommandError> {
    account_step(account, |a| a.back()).await
}

#[tauri::command]
pub async fn auth_submit_code(
    account: AccountState<'_>,
    code: String,
) -> Result<AuthView, CommandError> {
    account_step(account, move |a| a.submit_code(&code)).await
}

#[tauri::command]
pub async fn auth_retry_register(account: AccountState<'_>) -> Result<AuthView, CommandError> {
    account_step(account, |a| a.retry_register()).await
}

#[tauri::command]
pub async fn auth_sign_out(
    account: AccountState<'_>,
    local_only: bool,
) -> Result<AuthView, CommandError> {
    account_step(account, move |a| a.sign_out(local_only)).await
}

#[tauri::command]
pub async fn auth_set_server(
    account: AccountState<'_>,
    url: Option<String>,
) -> Result<AuthView, CommandError> {
    account_step(account, move |a| a.set_server(url.as_deref())).await
}

#[tauri::command]
pub fn open_settings(app: AppHandle) {
    windows::show(&app, windows::SETTINGS);
}

/// Which disk and tab the disk window opens on.
#[derive(Debug, Clone, Serialize)]
pub struct DiskWindowTarget {
    pub disk: Option<String>,
    /// `activity` or `trash`.
    pub tab: String,
}

/// Opens the disk window on a tab, on one disk when `disk` is given.
#[tauri::command]
pub fn open_disk(app: AppHandle, disk: Option<String>, tab: String) {
    windows::show(&app, windows::DISK);
    let tab = if tab == "trash" {
        tab
    } else {
        "activity".to_string()
    };
    let _ = app.emit_to(windows::DISK, "disk-window", DiskWindowTarget { disk, tab });
}

#[tauri::command]
pub fn open_welcome(app: AppHandle) {
    windows::show(&app, windows::WELCOME);
}

/// Marks the first run done and closes its window.
#[tauri::command]
pub fn finish_welcome(app: AppHandle, prefs: State<PrefsStore>) -> Result<(), CommandError> {
    prefs
        .update(|p| p.first_run_done = true)
        .map_err(io_error)?;
    windows::hide(&app, windows::WELCOME);
    Ok(())
}

#[tauri::command]
pub fn hide_panel(app: AppHandle) {
    windows::hide(&app, windows::PANEL);
}

#[tauri::command]
pub fn quit_app(app: AppHandle) {
    app.exit(0);
}
