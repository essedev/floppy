//! FAKE DATA. The control plane (accounts, teams, sign-in) is not built yet
//! (ROADMAP 3): until it is, the account shown in Settings and after sign-in
//! is this placeholder, and every surface that shows it says so.

use serde::Serialize;

/// The placeholder account, flagged with `fake: true` for the UI.
#[derive(Debug, Clone, Serialize)]
pub struct Account {
    pub fake: bool,
    pub name: &'static str,
    pub initials: &'static str,
    pub email: &'static str,
    pub team: &'static str,
    pub role: &'static str,
}

/// The example person and team from the concepts (docs/concepts/concept-c-app.html).
pub fn account() -> Account {
    Account {
        fake: true,
        name: "Giulia Ferri",
        initials: "GF",
        email: "giulia.ferri@studio.example",
        team: "Studio Esempio",
        role: "member",
    }
}
