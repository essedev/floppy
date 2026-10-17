//! The menu bar icon: one template image per state, from design/mark/
//! (rendered by scripts/render-marks.sh). Activity and start blink by
//! alternating two images every 0.55 s, as the concept specifies.

use crate::core::DiskStatus;
use crate::engine::{DiskState, JobKind};
use crate::text;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::image::Image;
use tauri::tray::TrayIcon;

const CONNECTED: &[u8] = include_bytes!("../icons/tray/connected.png");
const BLINK: &[u8] = include_bytes!("../icons/tray/blink.png");
const UNREACHABLE: &[u8] = include_bytes!("../icons/tray/unreachable.png");
const OFF: &[u8] = include_bytes!("../icons/tray/off.png");
const STARTING: &[u8] = include_bytes!("../icons/tray/starting.png");
const STARTING_BLINK: &[u8] = include_bytes!("../icons/tray/starting-blink.png");

const BLINK_PERIOD: Duration = Duration::from_millis(550);

/// What the menu bar icon shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Look {
    Connected,
    /// Connected and moving data (a warmup or a free the app started).
    Activity,
    Unreachable,
    Off,
    Starting,
}

impl Look {
    fn frames(self) -> (&'static [u8], Option<&'static [u8]>) {
        match self {
            Look::Connected => (CONNECTED, None),
            Look::Activity => (CONNECTED, Some(BLINK)),
            Look::Unreachable => (UNREACHABLE, None),
            Look::Off => (OFF, None),
            Look::Starting => (STARTING, Some(STARTING_BLINK)),
        }
    }
}

/// The look for a status.
pub fn look(status: &DiskStatus) -> Look {
    let busy = status
        .job
        .as_ref()
        .is_some_and(|j| matches!(j.kind, JobKind::Warmup | JobKind::Free));
    match status.state {
        DiskState::Connected if busy => Look::Activity,
        DiskState::Connected => Look::Connected,
        DiskState::Starting | DiskState::Stopping => Look::Starting,
        DiskState::Unreachable => Look::Unreachable,
        DiskState::Off | DiskState::NeedsSetup | DiskState::NotInstalled | DiskState::NoDisks => {
            Look::Off
        }
    }
}

/// The first image of every look, for building the tray icon.
pub fn initial_icon() -> tauri::Result<Image<'static>> {
    Image::from_bytes(STARTING)
}

/// Owns the tray icon's image and its blinking.
pub struct TrayState {
    icon: TrayIcon,
    current: Mutex<(Look, bool)>,
}

impl TrayState {
    /// Takes the tray icon and starts the blinking thread.
    pub fn new(icon: TrayIcon) -> Arc<Self> {
        let state = Arc::new(TrayState {
            icon,
            current: Mutex::new((Look::Starting, false)),
        });
        let blinker = Arc::clone(&state);
        thread::spawn(move || {
            loop {
                thread::sleep(BLINK_PERIOD);
                blinker.tick();
            }
        });
        state
    }

    /// Shows the look and tooltip for a status.
    pub fn show(&self, status: &DiskStatus) {
        let look = look(status);
        let busy = look == Look::Activity;
        let _ = self
            .icon
            .set_tooltip(Some(text::tooltip(status.state, busy)));
        let mut cur = self.current.lock().unwrap_or_else(|e| e.into_inner());
        if cur.0 != look {
            *cur = (look, false);
            self.set(look.frames().0);
        }
    }

    fn tick(&self) {
        let mut cur = self.current.lock().unwrap_or_else(|e| e.into_inner());
        let (first, second) = cur.0.frames();
        if let Some(second) = second {
            cur.1 = !cur.1;
            self.set(if cur.1 { second } else { first });
        }
    }

    fn set(&self, png: &'static [u8]) {
        if let Ok(img) = Image::from_bytes(png) {
            let _ = self.icon.set_icon(Some(img));
        }
    }
}
