// Prevents an additional console window on Windows in release, DO NOT REMOVE.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // `floppy-app mcp`: the MCP server for Claude on stdio, without the tray.
    if std::env::args().nth(1).as_deref() == Some("mcp") {
        std::process::exit(floppy_app_lib::run_mcp());
    }
    floppy_app_lib::run()
}
