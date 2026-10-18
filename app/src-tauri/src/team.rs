//! The team disks file, `~/.floppy/disks.json`: the control plane's answer
//! turned into the engine's input (docs/ARCHITECTURE.md, "Team disks"), with
//! no secret in it. The app writes it; `floppy disks apply` reads it. It also
//! keeps the person's own choice per disk (`enabled`), carried over every time
//! the file is rebuilt from a new answer.
//!
//! The engine refuses the whole file when one disk is wrong, so each disk is
//! checked here with the engine's own rules first: a disk that would not pass
//! is left out and shown as such, and the others still mount.

use crate::account::api::{Access, DeviceConfig};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// The file's name in the engine's home.
pub const FILE: &str = "disks.json";
/// The engine's defaults for the Mac-side cache ceiling and reserve.
pub const DEFAULT_CACHE_MAX_MB: u64 = 102_400;
pub const DEFAULT_RESERVE_MB: u64 = 20_480;
/// The engine's default when a disk says nothing.
pub const DEFAULT_CACHE_EXPIRE_DAYS: u32 = 30;

fn default_cache_max() -> u64 {
    DEFAULT_CACHE_MAX_MB
}
fn default_reserve() -> u64 {
    DEFAULT_RESERVE_MB
}
fn default_expire() -> u32 {
    DEFAULT_CACHE_EXPIRE_DAYS
}
fn default_true() -> bool {
    true
}

/// The disks file, version 1.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DisksFile {
    pub version: u32,
    /// The Mac's Valkey user, the same on every disk's instance.
    pub meta_user: String,
    /// PEM of the meta service's CA, or None for a public one.
    #[serde(default)]
    pub meta_ca: Option<String>,
    #[serde(default = "default_cache_max")]
    pub cache_max_mb: u64,
    #[serde(default = "default_reserve")]
    pub reserve_mb: u64,
    pub disks: Vec<FileDisk>,
}

/// One disk of the file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileDisk {
    pub id: String,
    /// The display name: the folder in /Volumes/Floppy.
    pub folder: String,
    pub slug: String,
    pub access: Access,
    pub meta_url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bucket_url: Option<String>,
    /// The bucket's S3 region, the server's: the mount signs with it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bucket_region: Option<String>,
    #[serde(default = "default_expire")]
    pub cache_expire_days: u32,
    /// False: the person turned the disk off on this Mac.
    #[serde(default = "default_true")]
    pub enabled: bool,
}

/// Why a disk of the server's answer was left out of the file.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RejectReason {
    /// A name the engine cannot use as a folder (DECISIONS #20: ASCII only).
    Name,
    /// Another disk has the same name without case.
    DuplicateName,
    /// An address the engine refuses (credentials, a query, an IP over TLS).
    Address,
    Id,
}

/// A disk the Mac may see but cannot mount, with the reason.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Rejected {
    pub id: String,
    pub name: String,
    pub reason: RejectReason,
}

/// The file in `home`.
pub fn path(home: &Path) -> PathBuf {
    home.join(FILE)
}

/// The file as it is now: None when there is none, an error when it is not
/// a disks file (it is then rebuilt from the next answer).
pub fn read(home: &Path) -> Result<Option<DisksFile>, String> {
    match std::fs::read(path(home)) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|e| format!("{} is not a disks file: {e}", path(home).display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// Writes the file through a temporary one, so the engine never reads half.
pub fn write(home: &Path, file: &DisksFile) -> std::io::Result<()> {
    std::fs::create_dir_all(home)?;
    let json = serde_json::to_vec_pretty(file).map_err(std::io::Error::other)?;
    let tmp = home.join(format!("{FILE}.tmp"));
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, path(home))
}

/// The file for the server's answer: every disk the Mac may see, in the
/// answer's order, with the person's choice and the Mac-side settings of the
/// previous file kept. A new disk starts turned on. The metadata CA is the
/// answer's; `meta_user` the answer's when it carries one.
pub fn build(
    config: &DeviceConfig,
    meta_user: &str,
    previous: Option<&DisksFile>,
) -> (DisksFile, Vec<Rejected>) {
    let mut disks: Vec<FileDisk> = Vec::new();
    let mut rejected = Vec::new();
    for d in &config.disks {
        let reason = if !valid_id(&d.id) {
            Some(RejectReason::Id)
        } else if !valid_folder(&d.name) {
            Some(RejectReason::Name)
        } else if !valid_meta_url(&d.meta_url) {
            Some(RejectReason::Address)
        } else if disks.iter().any(|o| o.folder.eq_ignore_ascii_case(&d.name)) {
            Some(RejectReason::DuplicateName)
        } else {
            None
        };
        if let Some(reason) = reason {
            rejected.push(Rejected {
                id: d.id.clone(),
                name: d.name.clone(),
                reason,
            });
            continue;
        }
        let before = previous.and_then(|p| p.disks.iter().find(|o| o.id == d.id));
        disks.push(FileDisk {
            id: d.id.clone(),
            folder: d.name.clone(),
            slug: d.volume_name.clone(),
            access: d.access,
            meta_url: d.meta_url.clone(),
            bucket_url: before.and_then(|b| b.bucket_url.clone()),
            bucket_region: d.region.clone().filter(|r| valid_region(r)),
            cache_expire_days: d.cache_expire_days,
            enabled: before.is_none_or(|b| b.enabled),
        });
    }
    let file = DisksFile {
        version: 1,
        meta_user: config
            .valkey_username
            .clone()
            .unwrap_or_else(|| meta_user.to_string()),
        meta_ca: config.meta_ca.clone(),
        cache_max_mb: previous.map_or(DEFAULT_CACHE_MAX_MB, |p| p.cache_max_mb),
        reserve_mb: previous.map_or(DEFAULT_RESERVE_MB, |p| p.reserve_mb),
        disks,
    };
    (file, rejected)
}

/// Turns a disk on or off in the file; false when the file has no such disk.
pub fn set_enabled(file: &mut DisksFile, id: &str, on: bool) -> bool {
    match file.disks.iter_mut().find(|d| d.id == id) {
        Some(d) => {
            d.enabled = on;
            true
        }
        None => false,
    }
}

/// The engine's rule for an id: lowercase letters, digits, hyphens.
pub fn valid_id(id: &str) -> bool {
    let b = id.as_bytes();
    (1..=63).contains(&b.len())
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
}

/// The engine's rule for a folder (DECISIONS #20): ASCII letters, digits,
/// space, dot, hyphen, underscore, starting and ending with a letter or digit.
pub fn valid_folder(name: &str) -> bool {
    let b = name.as_bytes();
    let edge = |c: &u8| c.is_ascii_alphanumeric();
    (1..=64).contains(&b.len())
        && b.first().is_some_and(edge)
        && b.last().is_some_and(edge)
        && b.iter()
            .all(|c| c.is_ascii_alphanumeric() || b" ._-".contains(c))
}

/// The engine's rule for a bucket region: 1-64 letters, digits, hyphens,
/// underscores. An empty or other one is left out of the file, and the mount
/// signs with JuiceFS's default.
pub fn valid_region(region: &str) -> bool {
    (1..=64).contains(&region.len())
        && region
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

/// The engine's rule for a metadata address: `rediss://<name>:<port>/<db>`
/// (or `redis://` for a local stack), no credentials, no query, and a name,
/// never an address, over TLS (the proxy routes by SNI, DECISIONS #25).
pub fn valid_meta_url(url: &str) -> bool {
    let (tls, rest) = if let Some(r) = url.strip_prefix("rediss://") {
        (true, r)
    } else if let Some(r) = url.strip_prefix("redis://") {
        (false, r)
    } else {
        return false;
    };
    let Some((host_port, db)) = rest.split_once('/') else {
        return false;
    };
    let Some((host, port)) = host_port.rsplit_once(':') else {
        return false;
    };
    let digits =
        |s: &str, max: usize| (1..=max).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_digit());
    let host_ok = !host.is_empty()
        && host
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'.' || c == b'-');
    let is_ip = host.bytes().all(|c| c.is_ascii_digit() || c == b'.');
    host_ok && digits(port, 5) && digits(db, 5) && !(tls && is_ip)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::account::api::{DiskConfig, OrganizationRef, UserRef};

    pub fn disk(id: &str, name: &str, access: Access) -> DiskConfig {
        DiskConfig {
            id: id.into(),
            name: name.into(),
            volume_name: name.to_lowercase().replace(' ', "-"),
            access,
            meta_url: format!(
                "rediss://{}.meta.example.com:6443/1",
                name.to_lowercase().replace(' ', "-")
            ),
            region: None,
            cache_expire_days: 30,
            mount_path: format!("/Volumes/Floppy/{name}"),
        }
    }

    pub fn config(disks: Vec<DiskConfig>) -> DeviceConfig {
        DeviceConfig {
            valkey_username: None,
            storage_mode: "manual".into(),
            s3_access_key_tail: None,
            meta_ca: None,
            organization: OrganizationRef {
                id: "o1".into(),
                name: "Studio Esempio".into(),
            },
            user: UserRef {
                email: "giulia@studio.test".into(),
                name: "Giulia Ferri".into(),
            },
            disks,
        }
    }

    #[test]
    fn builds_the_engine_file_and_keeps_the_persons_choice() {
        let c = config(vec![
            disk("0192-a", "Archivio 2025", Access::Read),
            disk("0192-b", "Showreel 2026", Access::Write),
        ]);
        let (first, rejected) = build(&c, "floppy_d_1", None);
        assert!(rejected.is_empty());
        let json = serde_json::to_value(&first).unwrap();
        assert_eq!(
            json["disks"][0],
            serde_json::json!({"id": "0192-a", "folder": "Archivio 2025", "slug": "archivio-2025",
                "access": "read", "meta_url": "rediss://archivio-2025.meta.example.com:6443/1",
                "cache_expire_days": 30, "enabled": true})
        );
        assert_eq!(json["meta_user"], "floppy_d_1");
        assert_eq!(json["meta_ca"], serde_json::Value::Null);
        assert_eq!(json["cache_max_mb"], 102400);

        let mut turned_off = first.clone();
        assert!(set_enabled(&mut turned_off, "0192-a", false));
        assert!(!set_enabled(&mut turned_off, "nope", false));
        turned_off.reserve_mb = 30720;
        // The access changed and a disk came: the choice and the reserve stay.
        let c2 = config(vec![
            disk("0192-a", "Archivio 2025", Access::Write),
            disk("0192-b", "Showreel 2026", Access::Write),
            disk("0192-c", "Nuovo", Access::Read),
        ]);
        let (second, _) = build(&c2, "floppy_d_1", Some(&turned_off));
        assert_eq!(second.reserve_mb, 30720);
        assert_eq!(second.meta_ca, None);
        let mut c3 = c2.clone();
        c3.meta_ca = Some("PEM".into());
        c3.valkey_username = Some("floppy_d_2".into());
        let (third, _) = build(&c3, "floppy_d_1", Some(&second));
        assert_eq!(third.meta_ca.as_deref(), Some("PEM"));
        assert_eq!(third.meta_user, "floppy_d_2");
        assert_eq!(second.disks[0].access, Access::Write);
        assert!(!second.disks[0].enabled);
        assert!(second.disks[1].enabled && second.disks[2].enabled);
    }

    #[test]
    fn carries_the_buckets_region_for_the_mount() {
        let mut garage = disk("0192-a", "Archivio", Access::Read);
        garage.region = Some("garage".into());
        let mut empty = disk("0192-b", "Showreel", Access::Read);
        empty.region = Some(String::new());
        let mut odd = disk("0192-c", "Nuovo", Access::Read);
        odd.region = Some("eu south".into());
        let older = disk("0192-d", "Vecchio", Access::Read);
        let (file, rejected) = build(&config(vec![garage, empty, odd, older]), "u", None);
        assert!(rejected.is_empty());
        let regions: Vec<Option<&str>> = file
            .disks
            .iter()
            .map(|d| d.bucket_region.as_deref())
            .collect();
        assert_eq!(regions, [Some("garage"), None, None, None]);
        let json = serde_json::to_value(&file).unwrap();
        assert_eq!(json["disks"][0]["bucket_region"], "garage");
        assert!(json["disks"][1].get("bucket_region").is_none());
        assert!(valid_region("eu-south-mil") && valid_region("us_east_1"));
        assert!(!valid_region("") && !valid_region("a/b") && !valid_region(&"a".repeat(65)));
    }

    #[test]
    fn leaves_out_what_the_engine_would_refuse() {
        let mut bad_url = disk("0192-d", "Ok", Access::Read);
        bad_url.meta_url = "rediss://user:pw@10.0.0.1:6443/1".into();
        let c = config(vec![
            disk("0192-a", "Città", Access::Read),
            disk("0192-b", "Showreel", Access::Read),
            disk("0192-c", "SHOWREEL", Access::Read),
            bad_url,
            disk("Upper", "Fine", Access::Read),
        ]);
        let (file, rejected) = build(&c, "u", None);
        assert_eq!(file.disks.len(), 1);
        let reasons: Vec<RejectReason> = rejected.iter().map(|r| r.reason).collect();
        assert_eq!(
            reasons,
            [
                RejectReason::Name,
                RejectReason::DuplicateName,
                RejectReason::Address,
                RejectReason::Id
            ]
        );
    }

    #[test]
    fn the_engine_rules() {
        assert!(valid_folder("Archivio 2025"));
        assert!(valid_folder("a"));
        assert!(!valid_folder(" a"));
        assert!(!valid_folder("a."));
        assert!(!valid_folder("Città"));
        assert!(!valid_folder(&"a".repeat(65)));
        assert!(valid_meta_url("rediss://archivio.meta.example.com:6443/1"));
        assert!(valid_meta_url("redis://localhost:4551/1"));
        assert!(valid_meta_url("redis://127.0.0.1:4551/1"));
        assert!(!valid_meta_url("rediss://127.0.0.1:6443/1"));
        assert!(!valid_meta_url("rediss://h:6443/1?x=1"));
        assert!(!valid_meta_url("rediss://u@h:6443/1"));
        assert!(!valid_meta_url("https://h:6443/1"));
        assert!(!valid_meta_url("rediss://h/1"));
        assert!(valid_id("0192f0c4-7d1e-7a40-9e52-3c1d2b9a0f11"));
        assert!(!valid_id("-a"));
    }

    #[test]
    fn reads_and_writes_the_file() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(read(dir.path()).unwrap(), None);
        let (file, _) = build(&config(vec![disk("0192-a", "A", Access::Read)]), "u", None);
        write(dir.path(), &file).unwrap();
        assert_eq!(read(dir.path()).unwrap(), Some(file));
        std::fs::write(path(dir.path()), "{").unwrap();
        assert!(read(dir.path()).is_err());
    }
}
