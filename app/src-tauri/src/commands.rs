//! Tauri commands: thin wrappers over `Core` and the windows. The frontend
//! calls them through `src/lib/backend.ts`.

use crate::core::{Core, CoreError, DiskStatus, Folder, SetupProgress};
use crate::engine::JobKind;
use crate::fake;
use crate::prefs::{Prefs, PrefsStore};
use crate::windows;
use serde::Serialize;
use std::process::Command;
use std::sync::Arc;
use tauri::{AppHandle, State};

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
            CoreError::Engine(_) => "engine",
        };
        CommandError {
            code,
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

#[tauri::command]
pub async fn list_folders(core: CoreState<'_>) -> Result<Vec<Folder>, CommandError> {
    let core = Arc::clone(&core);
    tauri::async_runtime::spawn_blocking(move || core.folders())
        .await
        .map_err(io_error)?
        .map_err(CommandError::from)
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

/// Facts about this app and this Mac for Settings and the first run.
#[derive(Debug, Serialize)]
pub struct AppInfo {
    pub version: String,
    pub engine_path: String,
    pub computer_name: Option<String>,
    /// Placeholder until the control plane exists: see `fake.rs`.
    pub account: fake::Account,
}

#[tauri::command]
pub async fn app_info(app: AppHandle, core: CoreState<'_>) -> Result<AppInfo, CommandError> {
    let computer_name = tauri::async_runtime::spawn_blocking(|| {
        Command::new("/usr/sbin/scutil")
            .args(["--get", "ComputerName"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .filter(|s| !s.is_empty())
    })
    .await
    .map_err(io_error)?;
    Ok(AppInfo {
        version: app.package_info().version.to_string(),
        engine_path: core.engine().display().to_string(),
        computer_name,
        account: fake::account(),
    })
}

#[tauri::command]
pub fn open_settings(app: AppHandle) {
    windows::show(&app, windows::SETTINGS);
}

#[tauri::command]
pub fn open_welcome(app: AppHandle) {
    windows::show(&app, windows::WELCOME);
}

/// Marks the first run done and closes its window.
#[tauri::command]
pub fn finish_welcome(app: AppHandle, prefs: State<PrefsStore>) -> Result<(), CommandError> {
    prefs
        .save(&Prefs {
            first_run_done: true,
        })
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
