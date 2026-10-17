//! The few strings the native side shows (tray tooltip and menu). Every other
//! string is in the frontend dictionary, `src/i18n/it.ts`; keep the two in the
//! same language and voice.

use crate::engine::DiskState;

pub const MENU_SETTINGS: &str = "Impostazioni…";
pub const MENU_QUIT: &str = "Esci da Floppy";

/// The tray tooltip for a state.
pub fn tooltip(state: DiskState, busy: bool) -> &'static str {
    match state {
        DiskState::Connected if busy => "Floppy: al lavoro",
        DiskState::Connected => "Floppy: collegato",
        DiskState::Starting => "Floppy: avvio",
        DiskState::Stopping => "Floppy: spegnimento",
        DiskState::Unreachable => "Floppy: non raggiungibile",
        DiskState::Off => "Floppy: spento",
        DiskState::NeedsSetup => "Floppy: da preparare",
        DiskState::NotInstalled => "Floppy: non installato",
    }
}
