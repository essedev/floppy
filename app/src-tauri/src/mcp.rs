//! This Mac's MCP server (DECISIONS #29): `floppy-app mcp` answers on stdio
//! for Claude Code, with what only this Mac can do: the disks' state, making
//! folders available or freeing them up, this Mac's activity and the
//! colleagues' (through the Mac's device token), the trash and undoing a
//! rename. The team's administration is the control plane's server, at
//! `/mcp/`.
//!
//! Newline-delimited JSON-RPC 2.0, protocol revision 2025-11-25 and the older
//! ones it negotiates down to; a method it does not know is `-32601`, which a
//! newer client reads as "fall back". Everything goes through the `floppy`
//! command (`engine`), like the app: this process never touches the VM, and
//! it never unmounts (CLAUDE.md, "Never unmount or remount a mounted disk
//! automatically"). Paths in and out are the Mac's, under the mount point.

use crate::account::api::{ActivityApi, ApiError};
use crate::activity::DeviceAuth;
use crate::engine::{self, EngineError, EngineReport};
use serde_json::{Value, json};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

/// The revisions this server speaks, newest first.
pub const PROTOCOLS: &[&str] = &["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];
/// Making a folder available downloads it whole: hours for a large project.
const WARMUP_TIMEOUT: Duration = Duration::from_secs(4 * 3600);
/// Freeing space drops cached blocks and trims the VM's disk.
const FREE_TIMEOUT: Duration = Duration::from_secs(30 * 60);
/// `floppy start` may start the VM first.
const START_TIMEOUT: Duration = Duration::from_secs(15 * 60);
/// At most this many paths per call: each is one command in the VM.
const MAX_PATHS: usize = 50;
const MAX_EVENTS: u64 = 200;

const INSTRUCTIONS: &str = "Floppy is the team's shared disk on this Mac: each disk is a folder in \
/Volumes/Floppy, its files on S3 and copied to the Mac when read. These tools act on this Mac: \
the disks' state, making a folder available offline (all of it copied now) or freeing up its \
space (the copies dropped, the files stay on the disk), this Mac's and the colleagues' activity, \
the trash and undoing a rename. Paths are the Mac's, under the mount point `status` reports. \
The team's administration (people, access, disks) is the `floppy` server of the control plane.";

/// The person's device token, when this Mac is signed in to a team.
pub trait DeviceSession {
    fn device(&self) -> Option<DeviceAuth>;
}

/// The server around the engine, the Mac's session and the control plane's client.
pub struct Server<'a> {
    engine: PathBuf,
    session: &'a dyn DeviceSession,
    api: &'a dyn ActivityApi,
}

/// A tool's failure, shown to the model as a result with `isError`.
struct ToolError {
    code: &'static str,
    message: String,
}

impl ToolError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        ToolError {
            code,
            message: message.into(),
        }
    }
}

impl From<EngineError> for ToolError {
    fn from(e: EngineError) -> Self {
        let code = match e {
            EngineError::Missing(_) => "not_installed",
            EngineError::Timeout(_) => "timeout",
            _ => "engine",
        };
        ToolError::new(code, e.to_string())
    }
}

impl From<ApiError> for ToolError {
    fn from(e: ApiError) -> Self {
        let code = if e.is_unauthorized() {
            "signed_out"
        } else {
            "control_plane"
        };
        ToolError::new(code, e.to_string())
    }
}

type ToolResult = Result<Value, ToolError>;

fn schema(properties: Value, required: &[&str]) -> Value {
    json!({ "type": "object", "properties": properties, "required": required, "additionalProperties": false })
}

fn paths_schema() -> Value {
    schema(
        json!({ "paths": {
            "type": "array",
            "items": { "type": "string" },
            "minItems": 1,
            "maxItems": MAX_PATHS,
            "description": "Files or folders under the mount point, e.g. /Volumes/Floppy/Progetti/Spot 2026."
        }}),
        &["paths"],
    )
}

/// The tools, in a fixed order: name, title, description, input schema, read-only, destructive.
fn tools() -> Vec<Value> {
    let tool = |name: &str,
                title: &str,
                description: &str,
                input: Value,
                read_only: bool,
                destructive: bool| {
        json!({
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": input,
            "annotations": {
                "title": title,
                "readOnlyHint": read_only,
                "destructiveHint": destructive,
                "idempotentHint": true,
                "openWorldHint": false,
            },
        })
    };
    let disk = json!({ "type": ["string", "null"], "description": "The disk's id from `status` (`disks[].id`); null for the single-user volume." });
    vec![
        tool(
            "status",
            "Floppy on this Mac",
            "Whether Floppy runs and the disk answers, where it is mounted, each team disk with its folder, access and state, and the space: copies on the Mac, their limit, free space. Call it first.",
            schema(json!({}), &[]),
            true,
            false,
        ),
        tool(
            "make_available",
            "Make available offline",
            "Copies files or folders to this Mac now, so they open without waiting and work offline (the Finder's \"Make available\"). Waits until done: a large folder takes as long as its download. Copies beyond the space limit push out the oldest ones.",
            paths_schema(),
            false,
            false,
        ),
        tool(
            "free_up_space",
            "Free up space",
            "Drops this Mac's copies of files or folders (the Finder's \"Free up space\"): they stay on the disk and download again when opened. Nothing is deleted.",
            paths_schema(),
            false,
            false,
        ),
        tool(
            "this_mac_activity",
            "This Mac's activity",
            "What this Mac created, wrote, renamed or deleted on the disks, oldest first after `after`, and the files it is writing and has open now.",
            schema(
                json!({
                    "after": { "type": "integer", "minimum": 0, "description": "The `last` of the previous call; 0 from the start." },
                    "limit": { "type": "integer", "minimum": 1, "maximum": MAX_EVENTS },
                }),
                &[],
            ),
            true,
            false,
        ),
        tool(
            "team_activity",
            "Team activity",
            "Who created, changed, renamed or deleted what on one team disk, colleagues included, newest first, for the last 30 days. Needs this Mac signed in to the team.",
            schema(
                json!({
                    "disk": { "type": "string", "description": "The disk's id from `status`." },
                    "before": { "type": "string", "description": "The `before` of the previous page." },
                }),
                &["disk"],
            ),
            true,
            false,
        ),
        tool(
            "list_trash",
            "Trash",
            "Every mounted disk's trash: deleted files and folders and the versions a save replaced, with where they were and when they expire.",
            schema(json!({}), &[]),
            true,
            false,
        ),
        tool(
            "restore_from_trash",
            "Restore from the trash",
            "Puts trash entries back where they were, never over a file: a taken name comes back as \"name (ripristinato)\", a missing folder at the top of the disk.",
            schema(
                json!({
                    "disk": disk,
                    "ids": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 500, "description": "Entry ids from list_trash." },
                }),
                &["disk", "ids"],
            ),
            false,
            false,
        ),
        tool(
            "undo_rename",
            "Undo a rename",
            "Gives a renamed or moved file or folder its name of before, from one of this Mac's `renamed` events; refused when the name is taken.",
            schema(
                json!({
                    "disk": disk,
                    "path": { "type": "string", "description": "The event's `path`, as the VM names it." },
                    "to": { "type": "string", "description": "The event's `to`." },
                }),
                &["disk", "path", "to"],
            ),
            false,
            false,
        ),
        tool(
            "start",
            "Start Floppy",
            "Starts Floppy on this Mac and mounts the disk when it is missing (the menu's Start). Only when the user asks or `status` shows it stopped: it never unmounts a mounted disk.",
            schema(json!({}), &[]),
            false,
            false,
        ),
    ]
}

/// A path inside the VM as the Mac's SMB client shows it (core.rs, `mac_path`).
fn mac_name(path: &str) -> String {
    const PRIVATE: &[u8] = b"\"*:<>?\\|";
    path.chars()
        .map(|c| match c as u32 {
            n @ 0xf001..=0xf01f => char::from((n - 0xf000) as u8),
            n @ 0xf020..=0xf027 => char::from(PRIVATE[(n - 0xf020) as usize]),
            _ => c,
        })
        .collect()
}

/// Where a disk's path is on this Mac, while it is mounted.
fn finder_path(report: &EngineReport, disk: Option<&str>, path: &str) -> Option<String> {
    let mp = report.mount_point.as_deref()?;
    let base = match disk {
        None => PathBuf::from(mp),
        Some(id) => Path::new(mp).join(&report.team_disks().iter().find(|d| d.id == id)?.folder),
    };
    Some(base.join(mac_name(path)).display().to_string())
}

/// Adds `finderPath` beside each `path` of the objects in `items`.
fn with_finder_paths(report: &EngineReport, items: &mut Value, disk_key: &str, path_key: &str) {
    let Some(list) = items.as_array_mut() else {
        return;
    };
    for item in list {
        let disk = item
            .get(disk_key)
            .and_then(Value::as_str)
            .map(str::to_string);
        let Some(path) = item
            .get(path_key)
            .and_then(Value::as_str)
            .map(str::to_string)
        else {
            continue;
        };
        if let Some(p) = finder_path(report, disk.as_deref(), &path) {
            item["finderPath"] = json!(p);
        }
    }
}

fn string_list(args: &Value, key: &str, max: usize) -> Result<Vec<String>, ToolError> {
    let list = args.get(key).and_then(Value::as_array).ok_or_else(|| {
        ToolError::new("validation", format!("`{key}` must be a list of strings"))
    })?;
    if list.is_empty() || list.len() > max {
        return Err(ToolError::new(
            "validation",
            format!("`{key}` takes 1 to {max} items"),
        ));
    }
    list.iter()
        .map(|v| {
            v.as_str().map(str::to_string).ok_or_else(|| {
                ToolError::new("validation", format!("`{key}` must be a list of strings"))
            })
        })
        .collect()
}

fn disk_arg(args: &Value) -> Result<Option<String>, ToolError> {
    match args.get("disk") {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) if !s.is_empty() => Ok(Some(s.clone())),
        _ => Err(ToolError::new(
            "validation",
            "`disk` must be a disk id or null",
        )),
    }
}

impl<'a> Server<'a> {
    /// A server running `engine`, signed in as `session`, calling the control plane with `api`.
    pub fn new(engine: PathBuf, session: &'a dyn DeviceSession, api: &'a dyn ActivityApi) -> Self {
        Server {
            engine,
            session,
            api,
        }
    }

    fn report(&self) -> Result<EngineReport, ToolError> {
        Ok(engine::status(&self.engine)?)
    }

    fn on_disk(&self, paths: &[String]) -> Result<EngineReport, ToolError> {
        let report = self.report()?;
        let mp = report.mount_point.clone().ok_or_else(|| {
            ToolError::new(
                "not_mounted",
                "The disk is not mounted: call `status`, then `start` if the user wants",
            )
        })?;
        if let Some(bad) = paths
            .iter()
            .find(|p| !engine::is_on_disk(Path::new(p), &mp))
        {
            return Err(ToolError::new(
                "not_on_disk",
                format!("{bad} is not under {mp}"),
            ));
        }
        Ok(report)
    }

    fn status(&self) -> ToolResult {
        let done = engine::run(&self.engine, &["status", "--json"], engine::STATUS_TIMEOUT)?;
        if !done.status.success() {
            return Err(ToolError::new(
                "engine",
                engine::last_message(&done.stderr).unwrap_or_default(),
            ));
        }
        let mut value: Value = serde_json::from_str(done.stdout.trim())
            .map_err(|e| ToolError::new("engine", format!("unexpected status: {e}")))?;
        let report = engine::parse_report(&done.stdout)?;
        if let (Some(mp), Some(disks)) = (
            report.mount_point.as_deref(),
            value.get_mut("disks").and_then(Value::as_array_mut),
        ) {
            for d in disks {
                if let Some(folder) = d.get("folder").and_then(Value::as_str) {
                    d["finderPath"] = json!(Path::new(mp).join(folder).display().to_string());
                }
            }
        }
        value["signedInToTeam"] = json!(self.session.device().is_some());
        Ok(value)
    }

    fn each_path(&self, args: &Value, command: &str, timeout: Duration) -> ToolResult {
        let paths = string_list(args, "paths", MAX_PATHS)?;
        self.on_disk(&paths)?;
        let mut done = Vec::new();
        for path in &paths {
            let finished = engine::run(&self.engine, &[command, path.as_str()], timeout)?;
            if !finished.status.success() {
                return Err(ToolError::new(
                    "engine",
                    format!(
                        "{path}: {}; done before it: {}",
                        engine::last_message(&finished.stderr).unwrap_or_else(|| "failed".into()),
                        if done.is_empty() {
                            "nothing".to_string()
                        } else {
                            done.join(", ")
                        }
                    ),
                ));
            }
            done.push(path.clone());
        }
        Ok(json!({ "done": done }))
    }

    fn this_mac_activity(&self, args: &Value) -> ToolResult {
        let after = args
            .get("after")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .to_string();
        let limit = args
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(100)
            .clamp(1, MAX_EVENTS)
            .to_string();
        let report = self.report()?;
        let done = engine::run(
            &self.engine,
            &["activity", "--after", &after, "--limit", &limit],
            engine::ACTIVITY_TIMEOUT,
        )?;
        if !done.status.success() {
            return Err(ToolError::new(
                "engine",
                engine::last_message(&done.stderr).unwrap_or_default(),
            ));
        }
        let mut value: Value = serde_json::from_str(done.stdout.trim())
            .map_err(|e| ToolError::new("engine", format!("unexpected activity: {e}")))?;
        for key in ["events", "writing", "open"] {
            if let Some(items) = value.get_mut(key) {
                with_finder_paths(&report, items, "disk", "path");
            }
        }
        Ok(value)
    }

    fn team_activity(&self, args: &Value) -> ToolResult {
        let disk = args
            .get("disk")
            .and_then(Value::as_str)
            .filter(|d| !d.is_empty())
            .ok_or_else(|| ToolError::new("validation", "`disk` is required"))?;
        let auth = self
            .session
            .device()
            .ok_or_else(|| ToolError::new("signed_out", "This Mac is not signed in to a team"))?;
        let before = args.get("before").and_then(Value::as_str);
        let page = self
            .api
            .activity_history(&auth.server, &auth.token, disk, before)?;
        let report = self.report().ok();
        let events: Vec<Value> = page
            .events
            .iter()
            .map(|e| {
                let mut v = json!({
                    "at": e.at, "op": e.op, "kind": e.kind, "path": e.path, "to": e.to,
                    "size": e.size, "by": e.by.name, "byYou": e.by.you, "thisMac": e.this_device,
                });
                if let Some(p) = report
                    .as_ref()
                    .and_then(|r| finder_path(r, Some(disk), &e.path))
                {
                    v["finderPath"] = json!(p);
                }
                v
            })
            .collect();
        Ok(json!({ "events": events, "before": page.before }))
    }

    fn list_trash(&self) -> ToolResult {
        let report = self.report()?;
        let trash = engine::trash(&self.engine)?;
        let mut value =
            serde_json::to_value(&trash).map_err(|e| ToolError::new("engine", e.to_string()))?;
        if let Some(disks) = value.get_mut("disks").and_then(Value::as_array_mut) {
            for d in disks {
                let disk = d.get("disk").and_then(Value::as_str).map(str::to_string);
                if let Some(entries) = d.get_mut("entries").and_then(Value::as_array_mut) {
                    for e in entries {
                        let from = e.get("from").and_then(Value::as_str).map(str::to_string);
                        if let Some(p) =
                            from.and_then(|f| finder_path(&report, disk.as_deref(), &f))
                        {
                            e["finderFolder"] = json!(p);
                        }
                    }
                }
            }
        }
        Ok(value)
    }

    fn restore(&self, args: &Value) -> ToolResult {
        let disk = disk_arg(args)?;
        let ids = string_list(args, "ids", 500)?;
        let ids: Vec<&str> = ids.iter().map(String::as_str).collect();
        let report = self.report()?;
        let restored = engine::restore(&self.engine, disk.as_deref(), &ids)?;
        let mut value =
            serde_json::to_value(&restored).map_err(|e| ToolError::new("engine", e.to_string()))?;
        if let Some(items) = value.get_mut("restored") {
            with_finder_paths(&report, items, "disk", "path");
        }
        Ok(value)
    }

    fn undo_rename(&self, args: &Value) -> ToolResult {
        let disk = disk_arg(args)?;
        let field = |key: &str| {
            args.get(key)
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .ok_or_else(|| ToolError::new("validation", format!("`{key}` is required")))
        };
        let (path, to) = (field("path")?, field("to")?);
        let done = engine::rename_back(&self.engine, disk.as_deref(), &path, &to)?;
        serde_json::to_value(&done).map_err(|e| ToolError::new("engine", e.to_string()))
    }

    fn start(&self) -> ToolResult {
        let done = engine::run(&self.engine, &["start"], START_TIMEOUT)?;
        if !done.status.success() {
            return Err(ToolError::new(
                "engine",
                engine::last_message(&done.stderr).unwrap_or_default(),
            ));
        }
        self.status()
    }

    fn call(&self, name: &str, args: &Value) -> Option<ToolResult> {
        Some(match name {
            "status" => self.status(),
            "make_available" => self.each_path(args, "warmup", WARMUP_TIMEOUT),
            "free_up_space" => self.each_path(args, "free", FREE_TIMEOUT),
            "this_mac_activity" => self.this_mac_activity(args),
            "team_activity" => self.team_activity(args),
            "list_trash" => self.list_trash(),
            "restore_from_trash" => self.restore(args),
            "undo_rename" => self.undo_rename(args),
            "start" => self.start(),
            _ => return None,
        })
    }

    /// The answer to one JSON-RPC message; None for a notification.
    pub fn handle(&self, message: &Value) -> Option<Value> {
        let id = message.get("id").cloned();
        let method = message.get("method").and_then(Value::as_str).unwrap_or("");
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        // A notification (no id) never gets an answer, a known one or not.
        let id = id?;
        let ok = |result: Value| json!({ "jsonrpc": "2.0", "id": id, "result": result });
        let err = |code: i64, text: &str| json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": text } });
        Some(match method {
            "initialize" => {
                let asked = params
                    .get("protocolVersion")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                let version = PROTOCOLS
                    .iter()
                    .find(|v| **v == asked)
                    .copied()
                    .unwrap_or(PROTOCOLS[0]);
                ok(json!({
                    "protocolVersion": version,
                    "capabilities": { "tools": { "listChanged": false } },
                    "serverInfo": { "name": "floppy-mac", "title": "Floppy on this Mac", "version": env!("CARGO_PKG_VERSION") },
                    "instructions": INSTRUCTIONS,
                }))
            }
            "ping" => ok(json!({})),
            "tools/list" => ok(json!({ "tools": tools() })),
            "tools/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or("");
                let args = params
                    .get("arguments")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                match self.call(name, &args) {
                    None => err(-32602, &format!("No such tool: {name}")),
                    Some(Ok(value)) => ok(result(value, false)),
                    Some(Err(e)) => ok(result(
                        json!({ "code": e.code, "message": e.message }),
                        true,
                    )),
                }
            }
            _ => err(-32601, &format!("Method not found: {method}")),
        })
    }

    /// Serves `input` until it closes, one JSON-RPC message per line.
    pub fn serve(&self, input: impl BufRead, mut output: impl Write) -> std::io::Result<()> {
        for line in input.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let answer = match serde_json::from_str::<Value>(&line) {
                Ok(message) if message.is_object() => self.handle(&message),
                _ => Some(
                    json!({ "jsonrpc": "2.0", "id": null, "error": { "code": -32700, "message": "Parse error" } }),
                ),
            };
            if let Some(answer) = answer {
                writeln!(output, "{answer}")?;
                output.flush()?;
            }
        }
        Ok(())
    }
}

fn result(value: Value, error: bool) -> Value {
    let structured = if value.is_object() {
        value.clone()
    } else {
        json!({ "result": value })
    };
    json!({
        "content": [{ "type": "text", "text": value.to_string() }],
        "structuredContent": structured,
        "isError": error,
    })
}

#[cfg(test)]
mod tests;
