//! Floppy's menu bar app (DECISIONS #9, #12): a tray icon with the disk's
//! state, a panel under it, Settings and a first-run window. The engine is the
//! `floppy` command; `core` runs it, this module wires it to Tauri.

mod commands;
mod core;
mod engine;
mod fake;
mod prefs;
mod text;
mod tray;
mod windows;

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
            commands::open_in_finder,
            commands::get_setup,
            commands::run_setup,
            commands::app_info,
            commands::open_settings,
            commands::open_welcome,
            commands::finish_welcome,
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

            let core = Core::new(engine::engine_path(), move |event| {
                on_event(&handle, &tray, event)
            });
            app.manage(Arc::clone(&core));
            let prefs = prefs::PrefsStore::new(&app.path().app_config_dir()?);
            let first_run = !prefs.load().first_run_done;
            app.manage(prefs);
            core.start_checking();

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
