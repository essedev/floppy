//! Floppy's menu bar app (DECISIONS #9, #12): a tray icon with the disk's
//! state, a panel under it, Settings and a first-run window. The engine is the
//! `floppy` command; `core` runs it, `account` signs the Mac in to the
//! control plane and hands `core` the team's disks, this module wires both
//! to Tauri.

mod account;
mod commands;
mod core;
mod engine;
mod installer;
mod prefs;
mod team;
mod text;
mod tray;
mod windows;

use crate::account::{Account, AccountEvent, Timing, api, secrets};
use crate::core::{Core, Event};
use std::sync::Arc;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, RunEvent, WindowEvent};

/// The login item's name (its LaunchAgent label), kept apart from the
/// engine's own agent, `dev.esse.floppy`.
const LOGIN_ITEM: &str = "dev.esse.floppy.menubar";

fn on_event(app: &tauri::AppHandle, tray: &tray::TrayState, event: Event) {
    match event {
        Event::Status(status) => {
            tray.show(&status);
            let _ = app.emit("status", &status);
        }
        Event::Setup(progress) => {
            let _ = app.emit("setup", &progress);
        }
        // The panel's webview renders it with the dictionary and posts it.
        Event::Notify(notice) => {
            let _ = app.emit_to(windows::PANEL, "notify", &notice);
        }
    }
}

fn on_account(app: &tauri::AppHandle, event: AccountEvent) {
    match event {
        AccountEvent::Changed(view) => {
            let _ = app.emit("auth", &view);
        }
        // The link was opened in the browser: back to the window that asked.
        AccountEvent::LinkOpened => {
            let welcome_open = app
                .get_webview_window(windows::WELCOME)
                .is_some_and(|w| w.is_visible().unwrap_or(false));
            windows::show(
                app,
                if welcome_open {
                    windows::WELCOME
                } else {
                    windows::SETTINGS
                },
            );
        }
    }
}

/// The engine this build carries, installed into the engine's home. None in
/// development (`tauri dev` bundles nothing) and when `FLOPPY_BIN` points the
/// app at another command, which an install would not replace.
fn bundled_engine(app: &tauri::App) -> Option<installer::Installer> {
    if std::env::var_os(engine::ENGINE_ENV).is_some_and(|p| !p.is_empty()) {
        return None;
    }
    let bundle = installer::Bundle::find(&app.path().resource_dir().ok()?)?;
    Some(installer::Installer {
        bundle,
        home: engine::floppy_home(),
    })
}

/// The macOS version as the server accepts it (`^[0-9A-Za-z.+-]+$`).
fn macos_version() -> String {
    std::process::Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|v| {
            !v.is_empty()
                && v.len() <= 40
                && v.chars()
                    .all(|c| c.is_ascii_alphanumeric() || ".+-".contains(c))
        })
        .unwrap_or_else(|| "unknown".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut app = tauri::Builder::default()
        // First, so a second launch exits before it adds a second tray icon.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            windows::show(app, windows::SETTINGS);
        }))
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name(LOGIN_ITEM)
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_status,
            commands::refresh_status,
            commands::start_disk,
            commands::stop_disk,
            commands::warmup,
            commands::free,
            commands::cancel_job,
            commands::dismiss_error,
            commands::list_folders,
            commands::set_disk_enabled,
            commands::restart_disk,
            commands::open_in_finder,
            commands::reveal_in_finder,
            commands::get_trash,
            commands::restore_trash,
            commands::open_trash,
            commands::get_setup,
            commands::run_setup,
            commands::install_engine,
            commands::app_info,
            commands::open_settings,
            commands::open_welcome,
            commands::finish_welcome,
            commands::auth_view,
            commands::auth_start,
            commands::auth_resend,
            commands::auth_back,
            commands::auth_submit_code,
            commands::auth_retry_register,
            commands::auth_sign_out,
            commands::auth_set_server,
            commands::hide_panel,
            commands::quit_app,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            let settings =
                MenuItem::with_id(app, "settings", text::MENU_SETTINGS, true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", text::MENU_QUIT, true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&settings, &quit])?;
            let icon = TrayIconBuilder::with_id("floppy")
                .icon(tray::initial_icon()?)
                .icon_as_template(true)
                .menu(&menu)
                // Left click opens the panel; the menu is on the right click.
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "settings" => windows::show(app, windows::SETTINGS),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|icon, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        rect,
                        ..
                    } = event
                    {
                        windows::toggle_panel(icon.app_handle(), rect);
                    }
                })
                .build(app)?;
            let tray = tray::TrayState::new(icon);

            let core = Core::new(
                engine::engine_path(),
                engine::floppy_home(),
                bundled_engine(app),
                move |event| on_event(&handle, &tray, event),
            );
            app.manage(Arc::clone(&core));
            let prefs = prefs::PrefsStore::new(&app.path().app_config_dir()?);
            let first_run = !prefs.load().first_run_done;
            app.manage(prefs.clone());
            core.start_checking();

            let version = app.package_info().version.to_string();
            let macos = macos_version();
            let account_handle = app.handle().clone();
            let account = Account::new(
                Arc::new(api::Http::new(&format!("Floppy/{version} (macOS {macos})"))),
                Arc::new(secrets::Keychain),
                Arc::clone(&core) as Arc<dyn account::TeamDisks>,
                prefs,
                api::Versions {
                    app_version: version,
                    macos_version: macos,
                },
                Timing::default(),
                move |event| on_account(&account_handle, event),
            );
            account.start_worker();
            app.manage(account);

            if let Some(panel) = app.get_webview_window(windows::PANEL) {
                let p = panel.clone();
                panel.on_window_event(move |event| {
                    if let WindowEvent::Focused(false) = event {
                        let _ = p.hide();
                    }
                });
            }
            if first_run {
                windows::show(app.handle(), windows::WELCOME);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .unwrap_or_else(|e| {
            eprintln!("floppy: failed to build the app: {e}");
            std::process::exit(1);
        });

    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);

    app.run(|app, event| match event {
        // Closing Settings or the first-run window hides it: the app stays in
        // the menu bar.
        RunEvent::WindowEvent {
            label,
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } => {
            api.prevent_close();
            windows::hide(app, &label);
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => windows::show(app, windows::SETTINGS),
        _ => {}
    });
}
