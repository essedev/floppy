//! The MCP server against fake `floppy` commands and a fake control plane:
//! the protocol, the tools' checks and the paths they give back.

use super::*;
use crate::account::api::{
    ActivityAnswer, ActivityPage, ActivityRequest, DeletesRequest, PersonRef, RemoteEvent,
};
use crate::engine::tests::{DISK_A, disk_with, fake, team_status};
use crate::engine::{Op, TrashKind};
use std::sync::Mutex;

struct Session(Option<DeviceAuth>);

impl DeviceSession for Session {
    fn device(&self) -> Option<DeviceAuth> {
        self.0.clone()
    }
}

#[derive(Default)]
struct Api {
    asked: Mutex<Vec<(String, Option<String>)>>,
}

impl ActivityApi for Api {
    fn activity(&self, _: &str, _: &str, _: &ActivityRequest) -> Result<ActivityAnswer, ApiError> {
        unreachable!("the MCP server never reports activity")
    }

    fn activity_history(
        &self,
        _: &str,
        token: &str,
        disk_id: &str,
        before: Option<&str>,
    ) -> Result<ActivityPage, ApiError> {
        assert_eq!(token, "device-token");
        self.asked
            .lock()
            .unwrap()
            .push((disk_id.to_string(), before.map(str::to_string)));
        Ok(ActivityPage {
            events: vec![RemoteEvent {
                id: "e1".into(),
                disk_id: disk_id.into(),
                at: "2026-10-22T09:00:00Z".into(),
                op: Op::Deleted,
                kind: TrashKind::File,
                path: "Spot/finale \u{f022} v2.mov".into(),
                to: None,
                size: Some(10),
                by: PersonRef {
                    name: "Sara".into(),
                    you: false,
                },
                this_device: false,
                local_id: 4,
            }],
            before: Some("next".into()),
        })
    }

    fn activity_deletes(
        &self,
        _: &str,
        _: &str,
        _: &DeletesRequest,
    ) -> Result<Vec<Option<PersonRef>>, ApiError> {
        unreachable!()
    }
}

fn signed_in() -> Session {
    Session(Some(DeviceAuth {
        server: "https://floppy.test".into(),
        token: "device-token".into(),
    }))
}

/// A fake engine: `status --json` answers the team status; every other call is
/// appended to `calls` and answers `body`.
fn engine(dir: &Path, body: &str) -> PathBuf {
    let status = team_status(&[DISK_A], "");
    let calls = dir.join("calls");
    fake(
        dir,
        &format!(
            "if [ \"$1 $2\" = 'status --json' ]; then echo '{status}'; exit 0; fi\necho \"$@\" >> '{}'\n{body}",
            calls.display()
        ),
    )
}

fn calls(dir: &Path) -> String {
    std::fs::read_to_string(dir.join("calls")).unwrap_or_default()
}

fn call(server: &Server, name: &str, arguments: Value) -> (bool, Value) {
    let answer = server
        .handle(&json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": { "name": name, "arguments": arguments } }))
        .unwrap();
    let result = &answer["result"];
    (
        result["isError"].as_bool().unwrap(),
        result["structuredContent"].clone(),
    )
}

#[test]
fn initialize_answers_the_version_asked_when_known() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    let answer = server
        .handle(&json!({ "jsonrpc": "2.0", "id": 7, "method": "initialize", "params": { "protocolVersion": "2025-06-18" } }))
        .unwrap();
    assert_eq!(answer["id"], 7);
    assert_eq!(answer["result"]["protocolVersion"], "2025-06-18");
    assert_eq!(answer["result"]["serverInfo"]["name"], "floppy-mac");
    let newer = server
        .handle(&json!({ "jsonrpc": "2.0", "id": 8, "method": "initialize", "params": { "protocolVersion": "2099-01-01" } }))
        .unwrap();
    assert_eq!(newer["result"]["protocolVersion"], PROTOCOLS[0]);
}

#[test]
fn notifications_get_no_answer_and_unknown_methods_are_32601() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    assert!(
        server
            .handle(&json!({ "jsonrpc": "2.0", "method": "notifications/initialized" }))
            .is_none()
    );
    let answer = server
        .handle(&json!({ "jsonrpc": "2.0", "id": 1, "method": "server/discover" }))
        .unwrap();
    assert_eq!(answer["error"]["code"], -32601);
    let unknown = server
        .handle(&json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": { "name": "format" } }))
        .unwrap();
    assert_eq!(unknown["error"]["code"], -32602);
}

#[test]
fn serve_answers_line_by_line_and_reports_bad_json() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    let input = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}\n\nnot json\n{\"jsonrpc\":\"2.0\",\"method\":\"notifications/cancelled\"}\n";
    let mut out = Vec::new();
    server.serve(input.as_bytes(), &mut out).unwrap();
    let lines: Vec<Value> = String::from_utf8(out)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(lines.len(), 2);
    assert_eq!(lines[0]["result"], json!({}));
    assert_eq!(lines[1]["error"]["code"], -32700);
}

#[test]
fn every_tool_schema_is_plain() {
    fn walk(v: &Value) {
        match v {
            Value::Object(map) => {
                if map.get("type") == Some(&json!("array")) {
                    assert!(map.contains_key("items"), "{v}");
                }
                assert!(!map.contains_key("$ref"));
                map.values().for_each(walk);
            }
            Value::Array(items) => items.iter().for_each(walk),
            _ => {}
        }
    }
    let list = tools();
    assert_eq!(list.len(), 9);
    for tool in &list {
        assert_eq!(tool["inputSchema"]["type"], "object");
        assert_eq!(tool["inputSchema"]["additionalProperties"], false);
        walk(tool);
    }
}

#[test]
fn status_gives_each_disk_its_finder_path() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (signed_in(), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    let (error, value) = call(&server, "status", json!({}));
    assert!(!error, "{value}");
    assert_eq!(
        value["disks"][0]["finderPath"],
        "/Volumes/Floppy/Archivio 2025"
    );
    assert_eq!(value["signedInToTeam"], true);
}

#[test]
fn make_available_refuses_paths_off_the_disk() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    for path in [
        "/Users/me/Desktop",
        "/Volumes/Floppy/../etc",
        "Volumes/Floppy/x",
    ] {
        let (error, value) = call(&server, "make_available", json!({ "paths": [path] }));
        assert!(error);
        assert_eq!(value["code"], "not_on_disk");
    }
    let (error, value) = call(&server, "make_available", json!({ "paths": [] }));
    assert!(error);
    assert_eq!(value["code"], "validation");
    assert_eq!(calls(dir.path()), "");
}

#[test]
fn make_available_warms_each_path_up() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    let paths = [
        "/Volumes/Floppy/Archivio 2025/Spot",
        "/Volumes/Floppy/Archivio 2025/Logo.psd",
    ];
    let (error, value) = call(&server, "make_available", json!({ "paths": paths }));
    assert!(!error, "{value}");
    assert_eq!(value["done"], json!(paths));
    assert_eq!(
        calls(dir.path()),
        format!("warmup {}\nwarmup {}\n", paths[0], paths[1])
    );
}

#[test]
fn a_failed_free_says_what_was_done_before_it() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let body = "case \"$2\" in *broken*) echo 'floppy: no such file in the VM' >&2; exit 1 ;; esac";
    let server = Server::new(engine(dir.path(), body), &session, &api);
    let (error, value) = call(
        &server,
        "free_up_space",
        json!({ "paths": ["/Volumes/Floppy/Archivio 2025/ok", "/Volumes/Floppy/Archivio 2025/broken", "/Volumes/Floppy/Archivio 2025/later"] }),
    );
    assert!(error);
    let message = value["message"].as_str().unwrap();
    assert!(message.contains("no such file in the VM"), "{message}");
    assert!(
        message.contains("done before it: /Volumes/Floppy/Archivio 2025/ok"),
        "{message}"
    );
    assert!(!calls(dir.path()).contains("later"));
}

#[test]
fn this_macs_activity_maps_samba_names_back_to_the_finder() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let body = "echo '{\"events\":[{\"id\":3,\"at\":1.0,\"disk\":\"0192-a\",\"op\":\"renamed\",\"kind\":\"file\",\"path\":\"A \u{f022} B.mov\",\"to\":\"C.mov\"}],\"last\":3,\"writing\":[],\"open\":[]}'";
    let server = Server::new(engine(dir.path(), body), &session, &api);
    let (error, value) = call(
        &server,
        "this_mac_activity",
        json!({ "after": 2, "limit": 5000 }),
    );
    assert!(!error, "{value}");
    // The Finder shows `A / B.mov`; the POSIX path has the colon.
    assert_eq!(
        value["events"][0]["finderPath"],
        "/Volumes/Floppy/Archivio 2025/A : B.mov"
    );
    assert_eq!(calls(dir.path()), "activity --after 2 --limit 200\n");
}

#[test]
fn team_activity_needs_a_signed_in_mac() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    let (error, value) = call(&server, "team_activity", json!({ "disk": "0192-a" }));
    assert!(error);
    assert_eq!(value["code"], "signed_out");
}

#[test]
fn team_activity_reads_the_history_with_the_device_token() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (signed_in(), Api::default());
    let server = Server::new(engine(dir.path(), ""), &session, &api);
    let (error, value) = call(
        &server,
        "team_activity",
        json!({ "disk": "0192-a", "before": "cursor" }),
    );
    assert!(!error, "{value}");
    assert_eq!(value["events"][0]["by"], "Sara");
    assert_eq!(
        value["events"][0]["finderPath"],
        "/Volumes/Floppy/Archivio 2025/Spot/finale : v2.mov"
    );
    assert_eq!(value["before"], "next");
    assert_eq!(
        *api.asked.lock().unwrap(),
        vec![("0192-a".to_string(), Some("cursor".to_string()))]
    );
}

#[test]
fn restore_passes_the_disk_and_the_entries() {
    let dir = tempfile::tempdir().unwrap();
    let (session, api) = (Session(None), Api::default());
    let body = "echo '{\"restored\":[{\"id\":\"2026-10-22-09/1-2\",\"disk\":\"0192-a\",\"path\":\"Spot/a.mov\",\"name\":\"a.mov\",\"renamed\":false,\"moved\":false}],\"failed\":[]}'";
    let server = Server::new(engine(dir.path(), body), &session, &api);
    let (error, value) = call(
        &server,
        "restore_from_trash",
        json!({ "disk": "0192-a", "ids": ["2026-10-22-09/1-2"] }),
    );
    assert!(!error, "{value}");
    assert_eq!(
        value["restored"][0]["finderPath"],
        "/Volumes/Floppy/Archivio 2025/Spot/a.mov"
    );
    assert_eq!(
        calls(dir.path()),
        "trash restore 0192-a 2026-10-22-09/1-2\n"
    );
    let (error, value) = call(
        &server,
        "restore_from_trash",
        json!({ "disk": 3, "ids": ["x"] }),
    );
    assert!(error);
    assert_eq!(value["code"], "validation");
}

#[test]
fn a_disk_not_mounted_is_said_so() {
    let dir = tempfile::tempdir().unwrap();
    let status = team_status(&[&disk_with(DISK_A, &[("state", "\"stopped\"")])], "").replace(
        "\"mount_point\":\"/Volumes/Floppy\"",
        "\"mount_point\":null",
    );
    let bin = fake(dir.path(), &format!("echo '{status}'"));
    let (session, api) = (Session(None), Api::default());
    let server = Server::new(bin, &session, &api);
    let (error, value) = call(
        &server,
        "make_available",
        json!({ "paths": ["/Volumes/Floppy/x"] }),
    );
    assert!(error);
    assert_eq!(value["code"], "not_mounted");
}
