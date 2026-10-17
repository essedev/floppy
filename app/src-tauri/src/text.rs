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

/// The page the browser shows on the sign-in loopback: done, or a link that
/// does not belong to the request the app is waiting on.
pub fn loopback_page(ok: bool) -> String {
    let (title, heading, body) = if ok {
        (
            "Floppy · Mac collegato",
            "Torna a Floppy",
            "Il Mac sta finendo l'accesso da solo. Puoi chiudere questa pagina.",
        )
    } else {
        (
            "Floppy · Link non valido",
            "Questo link non vale per questo Mac",
            "Torna all'app Floppy e chiedi un nuovo link.",
        )
    };
    format!(
        "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">\
         <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title>\
         <style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;background:#f4f2ee;color:#1c1b19;\
         display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}}\
         main{{max-width:420px;background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px #0002}}\
         h1{{font-size:20px;margin:0 0 8px}}p{{margin:0;color:#4a4740}}\
         @media (prefers-color-scheme:dark){{body{{background:#151413;color:#eee}}main{{background:#22211f}}\
         p{{color:#b5b0a6}}}}</style></head><body><main><h1>{heading}</h1><p>{body}</p></main></body></html>"
    )
}
