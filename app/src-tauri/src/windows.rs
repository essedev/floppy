//! The windows (declared in tauri.conf.json): the panel under the menu bar
//! icon, Settings, the trash and the first run. The app lives in the menu bar
//! (accessory, no Dock icon) and shows in the Dock only while one of the
//! others is open.

use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, Rect, Runtime};

pub const PANEL: &str = "panel";
pub const SETTINGS: &str = "settings";
pub const WELCOME: &str = "welcome";
pub const TRASH: &str = "trash";

/// The panel's width in points, as in the concept.
pub const PANEL_WIDTH: f64 = 352.0;

fn set_dock(app: &AppHandle<impl Runtime>, visible: bool) {
    #[cfg(target_os = "macos")]
    {
        let policy = if visible {
            tauri::ActivationPolicy::Regular
        } else {
            tauri::ActivationPolicy::Accessory
        };
        let _ = app.set_activation_policy(policy);
    }
    #[cfg(not(target_os = "macos"))]
    let _ = (app, visible);
}

/// Shows a window and brings it to the front.
pub fn show(app: &AppHandle<impl Runtime>, label: &str) {
    if label != PANEL {
        set_dock(app, true);
    }
    if let Some(w) = app.get_webview_window(label) {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// Hides a window; the Dock icon goes when no regular window is left.
pub fn hide(app: &AppHandle<impl Runtime>, label: &str) {
    if let Some(w) = app.get_webview_window(label) {
        let _ = w.hide();
    }
    let any_open = [SETTINGS, WELCOME, TRASH]
        .iter()
        .filter(|l| **l != label)
        .filter_map(|l| app.get_webview_window(l))
        .any(|w| w.is_visible().unwrap_or(false));
    if label != PANEL && !any_open {
        set_dock(app, false);
    }
}

/// Opens the panel under the menu bar icon, or closes it if open.
pub fn toggle_panel(app: &AppHandle<impl Runtime>, icon: Rect) {
    let Some(panel) = app.get_webview_window(PANEL) else {
        return;
    };
    if panel.is_visible().unwrap_or(false) {
        let _ = panel.hide();
        return;
    }
    let scale = panel.scale_factor().unwrap_or(1.0);
    let pos = icon.position.to_physical::<f64>(scale);
    let size = icon.size.to_physical::<f64>(scale);
    // Centred under the icon, in physical pixels on both sides.
    let x = pos.x + size.width / 2.0 - PANEL_WIDTH * scale / 2.0;
    let y = pos.y + size.height + 4.0 * scale;
    let _ = panel.set_position(PhysicalPosition::new(x.round() as i32, y.round() as i32));
    let _ = panel.show();
    let _ = panel.set_focus();
    let _ = app.emit_to(PANEL, "panel-opened", ());
}
