# Architecture

Floppy is a shared remote disk for teams that edit video: on a Mac it shows up as a volume in the Finder, the files live on S3 object storage, and the people working on it need to know nothing else. This document collects the analysis so far and the reasons behind the decisions, which are listed in [DECISIONS.md](DECISIONS.md). The project is between a test package (one disk per Mac, metadata in SQLite inside each VM, tested on an OVH bucket in Milan) and the team architecture: a team's disks shared by every Mac, as folders of one Floppy disk in the Finder, metadata in a central Valkey with a replica in each Mac's VM, a control plane for sign-in and access, a menu bar app. The team architecture is drawn in [architecture.html](architecture.html) and proven in a local spike; it is not built yet.

Prices and features of third-party services were checked on 06/10/2026, the alternatives again on 17/10/2026, and must be checked again before deciding.

## Guiding principle

**As simple as possible, and working.** Every extra component must solve an observed problem, not a hypothetical one. That is why the plan starts from a pilot with no app and no automation (see [ROADMAP.md](ROADMAP.md)) and builds only what the pilot proves necessary.

## Who uses it

- Several people, on Macs, not technical.
- They edit with **CapCut** and similar tools, not professional suites: no proxies, no project server, no collaboration handled by the editing software.
- The footage is mostly from phones (HEVC/H.264, 4K at about 50-100 Mbps).
- What they want is one thing: **a disk that shows up on the Mac** with the files on it.

## Why a plain network disk is not enough

Mounting from the cloud with SMB, NFS or a simple S3 mount works badly for two physical reasons:

- **Latency**: a European datacenter is 20-30 ms away. Scrubbing a video issues constant random reads, and each read pays that delay.
- **Bandwidth**: a 1 Gbps fiber line carries at most about 110 MB/s, an FTTC line much less. Upload is often the bottleneck when loading footage.

S3 is also not a filesystem: it is fine for copying whole files, not for reading scattered pieces the way a timeline does. What is needed is a **streaming filesystem** with a local cache, the category of LucidLink.

## Solution: JuiceFS on OVH

JuiceFS (Community Edition, open source) splits a file system the way LucidLink does: data as blocks on S3, metadata (names, folders, sizes) in a shared database, a local cache on every Mac. LucidLink also keeps a replica of the metadata on every client; Floppy gets the same effect with a patch on JuiceFS (see [Shared metadata over the WAN](#shared-metadata-over-the-wan)).

| Component | Choice | Role |
|---|---|---|
| Data | OVH Object Storage Standard bucket, versioning to be decided (see below) | The file blocks |
| Metadata | Valkey (Redis-compatible, BSD), central, durable, in the same region; a replica of it in each Mac's VM | File and folder tree, locks |
| Client | `juicefs` in a small Linux machine on each Mac, shared to the Mac over SMB on the private Mac-VM link | The volume in the Finder and the cache |

**Why a Linux machine and not `juicefs` directly on the Mac**: see [macFUSE](#macfuse). The Mac mounts an SMB network folder, which it can do on its own without extensions; JuiceFS runs in Linux, where FUSE is native. The price is one more component on each Mac.

**Region**: data and metadata as close as possible to the people editing. With the local replica, reads of the file tree no longer cross the network; every metadata write still costs a round trip to the central Valkey. Object Storage exists in Milan (region `EU-SOUTH-MIL`, 3-AZ): the test bucket lives there and answers in about 15 ms. Still to check: where the central Valkey runs (a VPS or a managed Valkey in Milan), and whether a cheaper 1-AZ region is enough for production.

**Why OVH**: about 8 $/TB per month and free egress within Europe. For video, egress matters more than space, because every clip that is not cached is downloaded again.

## Shared metadata over the WAN

Measured in the spike at a simulated 15 ms, from the Mac over SMB (DECISIONS #5, CYCLES 6):

| Operation | SQLite in the VM (one Mac) | PostgreSQL over the WAN | Valkey, no replica | Valkey with local replica |
|---|---|---|---|---|
| Cold listing of 300 files | 0.24 s | 117 s | 74.6 s | **0.20-0.30 s** |
| Creating 100 small files | 3.7 s | 174 s | 158 s | **13.7-17 s** |
| A file created on one Mac visible on another | n/a | n/a | 0.47 s | **0.00-0.01 s** |

**Why a round trip per operation does not work**: Samba turns one listing of 300 files into about 2,700 file system operations (lookups of `._` AppleDouble names, extended attributes for the Finder), each a round trip to the metadata. Inside the VM, without Samba, the same listing takes 0.12 s. The fix is not to make Samba ask less but to make the answers local.

**How the replica works**: the central Valkey pushes every write to the replicas as it executes it; each replica tracks an offset and resumes from a 256 MB backlog after a disconnection, or resyncs fully. JuiceFS (patched, `spike/juicefs-replica`, on v1.4.1) reads the tree, attributes, extended attributes and slice lists from the replica in its VM and writes to the master:

- **Read-your-writes**: each write transaction also increments a counter of the session; a read goes to the replica only once the replica has that value. No extra round trip.
- **Writes in one round trip**: JuiceFS's own logic runs on replica reads, then one script on the master checks that every value read is unchanged and applies the writes; a conflict retries the classic way (WATCH on the master). Create, setattr, setxattr, unlink and the commit of written data cost one round trip instead of 3-6. About 9 writes per file come from SMB, hence the 14-17 s for 100 files (DECISIONS #8).
- **Modes**, exposed in the mount's `.stats` (`juicefs_meta_replica_mode`): replica; master, when the replica lags beyond 2 s, its link is down or it restarted empty; offline, when the master is unreachable: reads keep coming from the replica, writes fail at once with "read-only file system". A slow master does not count as unreachable: offline needs an unambiguous network error or 10 s without any answer (`t13`, CYCLES 6).

**The JuiceFS patches**, on v1.4.1, in `spike/juicefs-replica/` until they move to a fork branch (architecture.html, The JuiceFS patches):

- **0001**: metadata reads from the local replica, writes in one round trip, read-your-writes (above).
- **0002**: whole-block reads inside a sequential stream (DECISIONS #15).
- **0003**: `juicefs config` and `juicefs format` take `ACCESS_KEY` and `SECRET_KEY` from the environment for an existing volume. Upstream they change only the flags passed on the command line (`ctx.LocalFlagNames()` in `cmd/config.go` and `cmd/format.go`, verified against Garage), so a team key rotation would put the secret in argv, visible in `ps`.
- **Possibly 0004**: `juicefs mount` taking the S3 key from the environment, for per-person and per-Mac keys (DECISIONS #22), if a format with no stored key does not already mount with the AWS SDK's chain against Garage and OVH.

**Why Valkey and not PostgreSQL**: one round trip per write through server-side scripts, and native replication simple enough to run a replica in each VM. Valkey and not Redis: Redis moved to source-available licenses in 2024 and to AGPL as an option in 2025; Valkey is BSD and in Debian 13 (8.1.1). The price is durability, which has to be configured: every write on disk before the answer (`appendfsync always`), a replica in the datacenter, a daily `juicefs dump` (see Backup).

**Versioning and Object Lock**: off on the test bucket. JuiceFS has its own trash; with versioning every block JuiceFS deletes or compacts stays in the bucket and is billed until it expires, and Object Lock would stop JuiceFS from deleting and compacting, so it stays off. With a team key versioning is recommended (DECISIONS #22): a revoked Mac that copied the key could delete blocks until the key is deleted at the provider, and versioning keeps them recoverable. A lifecycle rule expires old versions after 7 days, and the key's policy denies `s3:DeleteObjectVersion` and changes to versioning and lifecycle, or the copied key could erase the versions too. The control plane reads the bucket's versioning at every probe and shows it. With per-person or per-Mac keys a revoked key no longer exists, and versioning is optional.

## The cache

The cache lives on each Mac's SSD, inside the Linux machine's disk, managed by the JuiceFS client. It is what makes the disk fast.

- JuiceFS splits every file into 4 MB blocks. When a program reads a point of a video, only the blocks of that point are downloaded, and a copy stays in the cache. Next time they come from the SSD.
- **How blocks get in**: by reading, with prefetch downloading ahead during playback; with `juicefs warmup <folder>`, which preloads a whole project; and by writing, but only with `--cache-large-write`, which is off by default: whoever uploads the footage has it cached, the others do not.
- **They survive a restart**, until: the cache reaches `--cache-size` (the least recently used blocks go), free space drops below `--free-space-ratio` (default 10%), `--cache-expire` runs out if set, or they are freed by hand with `juicefs warmup --evict <folder>`. The blocks of a deleted file disappear at once on the Mac that deletes it; on the others they become orphans and are the first to go.
- **`--free-space-ratio` does not protect the Mac**: it looks at the Linux machine's disk, not the Mac's. In the package the cache ceiling and the emergency eviction are computed on the Mac side (see [macOS package](#macos-package)).
- **Every Mac has its own cache**: what one person has watched does not help the others.
- **Offline it is read-only**: the file tree lives in the central Valkey, but each VM keeps a replica of it. Without a network the disk can still be browsed and the cached blocks read; writes fail with "read-only file system" until the master answers again. Measured in the spike; the panel has to say it (ROADMAP 3).
- **Never stale data**: a modified file is written to new blocks, and the cached ones simply go unused.

## Measured performance

**On OVH Milan** (06/10/2026, macOS package with a Lima VM, test bucket, a home fiber line with 170 Mbps down, 167 up and 34 ms idle; OVH Milan answers in about 15 ms):

| Operation | Measure |
|---|---|
| Copying 2.7 GB to the disk | 113 s (23.9 MB/s, the line's limit) |
| Reading a 434 MB clip not in cache | 38 s (12.1 MB/s) |
| Reading from cache | 0.8 s |
| Seek (1 MB read) not in cache | mean 0.9 s, max 1.9 s |
| Seek in cache | 0-3 ms |
| Warmup of a 434 MB clip | 30 s (14.3 MB/s) |

- **Playback holds up even out of cache**: 12.1 MB/s against the 7.5 a 60 Mbps clip needs.
- **Scrubbing a clip never opened is slow**: almost a second per seek. Warmup removes it (3 ms). The data-path spike below cuts it with 1 MB blocks and a shorter readahead; to confirm here.
- SMB still has a cost: the line gives about 21 MB/s, the Mac gets 12 cold.

**Local spike, data path** (17/10/2026, `spike/data-bench.sh` and `t03` of `spike/acceptance.py`): 1 MB blocks, 4 MB readahead, the patched JuiceFS (DECISIONS #15). The link is one token bucket on everything Toxiproxy sends (`--profile shaped`) and 25 ms to S3; clips of 455 MB.

| Operation | 100 Mbps link | 1 Gbps link |
|---|---|---|
| Cold read from the Mac, 1 MB reads | 11.6 MB/s (93% of the line) | 103 MB/s (82%) |
| Cold read of a clip with `dd` (`t03`) | 10.8 MB/s (86%) | not measured |
| Seek (1 MB read) not in cache | mean 0.38 s, max 0.66 s | mean 0.09 s |
| Scrubbing: first read after a jump | mean 0.40 s, max 0.64 s | mean 0.08 s |
| Warmup of a clip | 38 s (95% of the line) | not measured |
| Copying a clip to the disk (300 Mbps upload limit) | 31.7 MB/s | not measured |

- **1 MB blocks halve a cold seek**: from 0.68 to 0.38 s at 100 Mbps, same link and readahead. A cold read downloads the whole block that holds it, 4 MB before. Uploads and warmup are as fast as with 4 MB blocks, and OVH bills neither requests nor traffic, so four times the requests cost nothing.
- **A short readahead keeps scrubbing fast**: after a jump, the readahead of the position just left keeps the line busy. First read after a jump at 100 Mbps: 0.40 s with 4 MB, 0.55 s with 16 MB, 0.78 s with JuiceFS's default (8 blocks). At 1 Gbps 4 MB costs sequential speed (82% of the line against 90%), still 13 times what a 60 Mbps clip needs.
- **The Mac reads ahead by itself**: through its cache the macOS SMB client sends 128 KB pieces, out of order and up to 40 MB ahead. JuiceFS turned the pieces into range requests, not cached, and downloaded the same blocks again: 12-13% extra. The second patch reads whole blocks inside a sequential stream: 4-6% extra, and GETs from 722 to about 550 for a clip.
- **"SMB costs half the line" was mostly the simulation**: JuiceFS's `--download-limit` lets the first megabyte through at once and then stalls, and the 4 MB blocks made every out-of-order piece expensive. On a real queue the Mac over SMB reads at 86-93% of the line, so SMB multichannel is not needed.
- **No read over 1.5 s** in any run, and no I/O error: the old `t03` failure does not come back.

**Local spike, first round** (06/10/2026, simulation before OVH, 4 MB blocks):

Measures from the 06/10/2026 spike (`spike/bench.sh`): an Apple Silicon Mac, Garage in Docker as S3, 25 ms of latency simulated with Toxiproxy, bandwidth limited with JuiceFS's `--download-limit` and `--upload-limit`. Test clips: 4K HEVC at 60 Mbps, 434 MB each, close to phone footage.

| Operation | 100/300 Mbps link | 1 Gbps link |
|---|---|---|
| Copying 2.7 GB to the disk | 86 s (about 31 MB/s, upload limit) | not measured |
| Reading a clip not in cache | 68 s (6.7 MB/s) | 14 s (33 MB/s) |
| Reading a cached clip | 1.5 s (about 290 MB/s) | same |
| Seek (1 MB read) not in cache | mean 0.8 s, max 1.6 s | mean 0.2 s, max 2.9 s |
| Seek in cache | mean 2-5 ms | same |
| Warmup of a clip | 43 s | not measured |

- **In cache it is like a local disk**: the cache sits in the Linux machine and SMB on `localhost` sustains about 160-290 MB/s.
- **Out of cache the line decides**: a 60 Mbps clip needs 7.5 MB/s. At 1 Gbps uncached playback has 4 times the margin; at 100 Mbps it does not hold (6.7 MB/s) and warming up before editing becomes mandatory.
- **SMB seemed to cost half the cold bandwidth**: inside the Linux machine 10.7 MB/s on 100 Mbps, over SMB 6.7. The data-path spike (above) found the cause in the simulated link and in 4 MB blocks.
- **A cold seek pays for the whole block**: reading 1 MB at a new point downloads the 4 MB block that holds it.
- Local Garage in Docker is not OVH: the measures isolate latency and bandwidth, not the real service's performance. They were repeated on OVH (above).
- The first opening of a folder with many videos is slow, because the Finder and CapCut read a piece of every file for the previews. Not measured yet.

## Configuration

| Setting | Value | Reason |
|---|---|---|
| Compression | off | Video is already compressed |
| Block size | 1 MB | Halves a cold seek against the 4 MB default, with uploads and warmup as fast (DECISIONS #15). Fixed when the volume is created |
| Trash | 7 days | Recovery from accidental deletions |
| `writeback` | off | A file counts as saved only once it is on S3. Slower copies, but no file left only on a Mac closed halfway through an upload |
| `--cache-size` | computed at every `setup`, at most `CACHE_SIZE_MB` (100 GB by default) | Half of what the Mac can spare above a reserve (see [macOS package](#macos-package)) |
| `--cache-expire` | 30 days | Old projects free themselves |
| `--cache-large-write` | on | Whoever uploads footage usually edits it: uploaded blocks stay cached. By default JuiceFS does not keep them |
| `--prefetch` | 8 | Measured with 4 MB blocks as the best value cold over SMB; with 1 MB blocks 1, 3 and 8 measure the same |
| `--max-readahead` | 4 MB | Scrubbing: the first read after a jump at 100 Mbps in 0.40 s against 0.78 s with the default (DECISIONS #15) |
| `--buffer-size` | 256 MB | Measured: as fast as 1 GB and leaves room in a 1 GiB VM (see "Footprint") |
| `--enable-xattr` | on | Samba stores the Finder's metadata there; without it, deleting from the Mac fails with "Operation not supported" |
| Metadata cache | JuiceFS defaults | With the local replica reads are local, so longer kernel caches buy little and would delay other people's changes. JuiceFS 1.4's Redis client-side cache (`client-cache=true`) stays off: the patch refuses it (DECISIONS #5) |
| Samba | `fruit` module (with `samba-vfs-modules`), signing and encryption off, asynchronous I/O | Finder compatibility; signing off and asynchronous I/O take cached reads from 69 to 163 MB/s. Off only because SMB stays on the private Mac-VM link |
| Samba, hidden files | `veto files` on `.accesslog`, `.config`, `.stats`, `.trash` | JuiceFS's internal files do not show up in the Finder |
| Spotlight | `mdutil -i off` on the volume | No indexing that reads everything |

## Working with CapCut

- **Media live on Floppy, CapCut drafts stay on the Mac**: CapCut saves projects in its local folder and references videos by the path they are at.
- **A draft has a single owner**: two people cannot open the same draft. To hand a project over, use CapCut's cloud space or an export.
- **One folder per project**, holding the footage and the exports. No further structure until the pilot shows a need for it.
- **To check in the pilot**: how CapCut reacts to a video that is slow to arrive. If it errors out or loses the link to the file, warming up before editing becomes mandatory.

## Interface

JuiceFS Community has no end-user interface at all: it is a terminal program. People editing see only a volume in the Finder; everything else comes from Floppy.

**A menu bar app in Tauri** (DECISIONS #9), built on the skeleton of [Portsage](https://github.com/essedev/portsage), which already has a menu bar icon and panel, launch at login and an external binary bundled in the app (`externalBin`). The `floppy` command stays the engine the app drives. Swift would only be needed for native Finder badges and menus, which a Swift extension could add next to the app.

**Designed surface by surface**, each in a concept with the decisions set (DECISIONS #11, #12):

- [concept-c-app](concepts/concept-c-app.html): the panel in every state (connected, downloading, starting, unreachable with automatic reconnection every 30 s, off, revoked, Mac nearly full, update available), the project menu, the Settings window, notifications, the menu bar icon. It re-renders from a row of choices.
- [concept-d-onboarding](concepts/concept-d-onboarding.html): from `brew install` to the disk in the Finder, sign-in in the browser with a magic link and a 6-digit code, an invited colleague, the edge cases.
- [concept-e-admin](concepts/concept-e-admin.html): the control plane's web: people, Macs, disks, the S3 key rotation, the log, settings.
- [concept-b-identity](concepts/concept-b-identity.html): the identity, the drive with a Grafite faceplate and the slot-and-LED mark.

The problems each feature answers, to verify in the pilot:

| Problem | Feature |
|---|---|
| The disk does not show up after sleep or a network change | Mount at login, the state on the faceplate's LED, automatic reconnection |
| This clip stutters | How much of a project is already on the Mac, "Make available" with progress |
| The Mac is full | Space used by the cache, freeing per project, the least recently used blocks freed when the cache is full |
| Credentials on every Mac | Sign-in in the browser, one revocable user per Mac |
| The network drops | Read-only browsing from the local replica, stated in the panel |
| It does not work and nobody knows why | "Send diagnostics" with logs and status |

**macOS only for now, with the system layer isolated.** The interface (status, cache, warmup) is shared; only how JuiceFS runs changes, behind a single interface (mount, unmount, status, warmup, free up space) with one module per system selected by `#[cfg(target_os)]`, like Portsage's scanner:

| System | JuiceFS | Linux machine |
|---|---|---|
| macOS | in a light Linux VM, shared over SMB | yes, the only way without a kernel extension |
| Windows | native `juicefs.exe` with WinFsp, a drive letter | no |
| Linux | native with FUSE | no |

Only the macOS module is built; Windows is added as a module, not a rewrite, when needed.

**Distribution with Homebrew** (DECISIONS #10), like Portsage: a cask in its own tap, without an Apple Developer ID, clearing the quarantine in `postflight_steps`. Homebrew needs administrator rights once to install itself; the app and the disk never do. Limits: updates need `brew upgrade`, and the trick for unsigned apps depends on Homebrew's rules, which are getting stricter. A DMG in `~/Applications` stays the fallback.

## Access and security

- **Access per person and per disk** (DECISIONS #23): each person has, on each disk, no access, read-only or read-write, decided by admins; a device inherits its person's. A replica copies its whole instance and a key opens its whole bucket, so access is enforced where the data is: every disk has its own Valkey instance and its own bucket, and a device gets a replica, a mount and a user only for the disks it may see. A new disk is read-write for the admin who creates it and invisible to everyone else unless the creation lists them; a new person sees no disk unless the invitation lists some (DATABASE_SCHEMA.md, "Access per disk").
- **One Valkey password per device** (DECISIONS #7), used for a user of the same name on each disk's instance it may see, whose ACL is the access: read-only is reads plus what replication and the patch's probe need, read-write adds writes, transactions and the scripts (the lists verified in `spike/perdisk.py`). A read-only mount writes nothing, and one tampered to mount read-write fails at session creation. Changes apply live: the ACL changes in place, so lowering to read-only makes writes fail at once and the heartbeat tells the app to remount the disk read-only; lowering to none deletes the user. Revoking a device deletes its users everywhere; the app then stops its disks and clears cache and replicas.
- **Two storage modes** (DECISIONS #24). **Manual**, for any S3: the admin creates one bucket and one key per disk, limited to that bucket, and pastes them when creating the disk; the key is in the disk's format, which a device receives only for the disks it may see. Read-only is enforced on the metadata, but that key can write the bucket, so bucket versioning is recommended. Removing someone who could see a disk, revoking one of their devices or lowering them to none opens the rotation window of that disk's key alone: a new key pasted, applied with `juicefs config`, the old one deleted at the provider (DECISIONS #18). **Managed**: the adapter (Garage built; OVH, AWS, MinIO to come) creates each disk's bucket, owned by the control plane's own key, and gives each device one key, granted read or read-write per bucket and changed in place when access changes; revocation deletes the key. The format then holds no key and each mount brings its device's own from the environment. The hosted service always uses managed.
- **OVH S3 keys open every bucket in the Public Cloud project**, not just their own. A key needs a policy limited to its bucket, set in the provider console (manual mode) or by the adapter (managed mode).
- **TLS to the disks' instances** (DECISIONS #21, #25): devices reach every instance across the internet with their own password, for writes and for their replicas' streams, on one TLS port where the reverse proxy routes by the name in the handshake (`<disk>.meta.<domain>`). SMB stays plain only because it never leaves the Mac.
- **Inside a disk, permissions are all or nothing**: whoever may write a disk sees and can delete everything in it. Trash and replica mitigate deletions. Per-folder control is not offered by JuiceFS Community; SpaceFS and LucidLink do it.

## Control plane

The service that hands out access: organizations, people, devices, disks with their instances, buckets and keys, who may see each disk, rotations, and the audit log, with sign-in by magic link (DECISIONS #2, #12). **Built** in `server/`: sign-in by link, code and invitation, the app's enrolment and bootstrap, people, devices, disks created with their own instance and bucket, access per person and disk applied live on the instances and at the provider, the rotation of a manual disk's key, settings, health per disk, the reconciler and the background jobs, with integration tests on PostgreSQL, Valkey, Garage and JuiceFS. FastAPI and React with its own PostgreSQL, AGPL-3.0 (DECISIONS #3), never on the data path: if it stops, mounted disks keep working and only sign-ins, invitations, access changes, revocations and rotations wait. The data model, the secrets at rest and the state machines are in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md); the parts, flows and failure modes are drawn in [architecture.html](architecture.html).

**What lives outside PostgreSQL is applied after the commit.** An access change, a registration or a revocation writes the wanted state of each device's user on each instance (and, managed, of each key's grant on each bucket) with the change, then applies it; what an instance or the provider does not take stays owed, and the reconciler applies it, then compares every instance's users with what they should be. A change is durable before it is effective, and a restored instance gets its users back from the password hashes kept in PostgreSQL.

**The disks' instances come from a meta service** (DECISIONS #25) behind one interface: create an instance for a disk, release it. The service with one Valkey per disk behind the TLS proxy is not built yet; development and the tests use a pool of Valkey instances from the Compose file, each handed to one disk, so the code path is the same. The control plane never starts containers.

What self-hosting needs, besides the Compose file:

- **PostgreSQL 18**, for `uuidv7()` (DECISIONS #16); the first migration refuses an older server.
- **`FLOPPY_SECRET_KEY`**: 32 random bytes that encrypt the disks' S3 secrets, the instances' admin passwords, the storage adapter's credentials and the SMTP password at rest (DECISIONS #17). Losing it loses no files: a manual disk's key is still in its format and an admin rotates it; in managed mode the adapter's credentials are typed again and it creates a new key for the control plane; the SMTP password is typed again.
- **The storage mode**, chosen at first start: manual with a bucket and a key pasted per disk and any S3, or managed with a provider adapter and its admin credentials (DECISIONS #24). It is fixed once a disk exists.
- **One Valkey instance per disk**, reachable by the devices over TLS: today a pool (`FLOPPY_META_POOL`) with its admin password in the environment; the number of disks is what the pool, or later the meta service, can run.

## Backup and resilience

- **ACL users do not replicate**: Valkey keeps them in each instance's configuration, so a promoted replica or a master restored from a dump has none of the devices' users. The control plane keeps each device's password as a SHA-256 in PostgreSQL (the form Valkey stores) and the access of each device on each disk, and a reconciler recreates the users with their ACL and deletes the ones with no access, on every disk's instance, at start and every few minutes: no device signs in again (DECISIONS #7, #23).
- **The metadata is the critical point**: lose it and the filesystem is lost, because the blocks on S3 alone cannot be put back together into files. The central Valkey writes every change to disk before answering (`appendfsync always`), has a replica in the datacenter, and a daily `juicefs dump` is kept outside it. The replicas in the Macs' VMs are not a backup: they follow the master, deletions included.
- **JuiceFS is not a backup**: the bucket has to be replicated to a second provider (Backblaze B2, about 7 $/TB) with versioning.
- **Archive**: closed projects leave JuiceFS and go with `rclone` to a separate bucket, as normal files, readable even without the database.
- Minimal monitoring: an alert if the database or the bucket stops answering. JuiceFS exposes Prometheus metrics.

## macFUSE

**JuiceFS on the Mac requires macFUSE's kernel extension**, verified in the spike on macOS 26.6 with JuiceFS 1.4.1 and macFUSE 5.4.0:

- **FUSE-T does not work**: JuiceFS does not use libfuse but talks directly to macFUSE's component, and fails with `no FUSE mount utility found`.
- **macFUSE's FSKit backend is not enough**: macFUSE supports it only through libfuse3. With `-o backend=fskit` the mount hangs and macOS asks to enable kernel extensions.
- **The kernel extension** requires, Mac by Mac, shutting down, booting into recovery mode and setting startup security to "Reduced". For non-technical users it is the opposite of the guiding principle.

Hence the Linux machine with SMB: no extension on the Mac. In the spike it was Docker Desktop, too heavy to ask of a video editor; in the package it is a Lima VM (see [macOS package](#macos-package)).

## macOS package

The macOS module: a command (`macos/bin/floppy`), the engine the Tauri app drives. `scripts/build-engine.sh` assembles it with everything the VM needs and no secrets; the app carries it inside its bundle and installs it on first run, and until the team version configures the disk from the sign-in the tester zip (`scripts/package.sh <config.env>`) carries the same engine plus a `config.env`, installed with `bash install.sh` from the Terminal.

- **Inside the app**: `scripts/build-app.sh` (`make app`) builds Floppy.app with Tauri and copies the engine into `Contents/Resources/engine`, with a `BUILD` file holding the hash of its files; the app grows from 6 to about 555 MB, the cask's DMG (LZMA) is about 410 MB. The bundle is signed ad hoc as a whole, without `--deep`: the Mach-O files inside keep their own signatures, and `limactl` keeps Lima's ad hoc signature with the `com.apple.security.virtualization` entitlement, without which the VM does not start. The build checks the entitlement after signing; a VZ VM was booted with the `limactl` copied out of the bundle. `SIGN_IDENTITY` signs with a real identity instead.
- **First run and updates** (`app/src-tauri/src/installer.rs`): the app runs the bundled `install.sh`, the same script as the zip, with `FLOPPY_HOME` pointing at the engine's home: it replaces `lima`, `bin` and `vm` in `~/.floppy`, keeps configuration, VM and volume, and runs `floppy setup`. Once it succeeds the app writes the bundle's `BUILD` into `~/.floppy/engine-build`; an engine with another build (an older app, or the zip) is offered as an update. The first-run window installs the engine on a fresh Mac and shows the progress; on a Mac that already has the disk the update waits for the user ("Update now" in the panel), because `setup` restarts JuiceFS and Samba in the VM and would break a copy in progress. `tauri dev` and a `FLOPPY_BIN` override carry no engine and install nothing.
- **Homebrew** (DECISIONS #10): the cask's source is `packaging/homebrew/Casks/floppy.rb`; `scripts/release-app.sh` builds the DMG, tags, publishes the GitHub release and writes the cask with version and sha256 into the tap `essedev/homebrew-floppy` (`packaging/homebrew/render.sh`). Homebrew quarantines every cask download and no longer has `--no-quarantine`; Gatekeeper is enforced only on the official `homebrew/cask`, while a third-party tap may clear the flag. The cask does it for Floppy.app alone in `postflight_steps` (the declarative form `brew style` requires; Relay's tap does the same), and the engine copied out of the bundle inherits no flag. Since Homebrew 6 a third-party tap needs trust: the fully qualified `brew install --cask essedev/floppy/floppy` trusts that cask only. Uninstalling the cask quits and removes the app and leaves `~/.floppy`, whose VM holds the metadata; `zap` removes the app's own files and login item.

- **Lima 2.2.1 VM**, official binary bundled in the zip and verified with the release checksum. `vz` backend (Apple's Virtualization framework), Debian 13, 2 CPUs, 1 GiB of RAM, a sparse disk as large as the cache ceiling plus 10 GiB, fixed at creation. No administrator rights: everything lives in `~/.floppy`, with the VMs in their own `LIMA_HOME` that does not touch other Lima installs.
- **`vzNAT` network**: the VM has an address on the Mac (e.g. `192.168.64.2`) and SMB goes there directly, without the port forwarding that cost bandwidth in the Docker spike. Lima's automatic forwarding is off (`portForwards` with `ignore`), so the VM exposes no ports on the Mac.
- **Inside the VM**: `vm/guest/guest-install.sh` installs Samba, the patched JuiceFS and two systemd services, the mount (`floppy-mount`, which formats the volume on first start) and `smbd`, which starts only once the mount is ready. It can be rerun safely to update.
- **On the Mac**: the volume is mounted with `osascript` (`mount volume`) and shows up in `/Volumes/Floppy` and in the Finder sidebar; Spotlight is off on the volume; a launchd agent (`dev.esse.floppy`) runs `floppy tick` at login and every 2 minutes (see "Agent").
- **Mount point**: in the team architecture every disk of the organization is a folder of the one share, `/Volumes/Floppy/<disk>`: each disk is its own JuiceFS mount inside the VM under the share's root, which is read only so nothing lands on the VM's disk, and the folder is the disk's display name, never renamed (DECISIONS #20). The mount point itself must stay `/Volumes/Floppy`, because CapCut projects reference videos by full path and with `Floppy-1` the links break. macOS picks `-1` when the folder of a previous mount still exists: the command waits for it to disappear before mounting, and logs a warning if the name changes anyway. Commands still read the mount point from `mount`.
- **SMB timeout**: macOS fails an SMB read after 30 s without an answer. The command raises the limit to 600 s only for the VM's address, in its own block inside `~/Library/Preferences/nsmb.conf` (per user, no administrator). An I/O error showed up once at a simulated 100 Mbps, with a cold read started right after a 2.7 GB upload; it did not come back in the A/B comparison, and the limit stays as a guard for slow lines.
- **Space on the Mac** (incident of 06/10, see [CYCLES.md](CYCLES.md)): the VM does not see the Mac's disk, and JuiceFS's space check looks only at the VM's. On a tester's Mac with 10 GB free the cache filled the disk, the VM's file could not grow any more and its filesystem failed. Now `setup` computes the cache ceiling at every run: half of what the Mac can spare above `RESERVE_MB` (20 GB by default), counting the space the cache already takes as spare, at most `CACHE_SIZE_MB`. Below 5 GB `setup` refuses. The agent evicts the whole cache when the Mac drops below half of the reserve, with a notification.
- **Space given back**: the VM's disk file grows but does not shrink on its own; freed space goes back to the Mac with `fstrim`, which Debian runs weekly. `floppy free` and the emergency eviction run it at once (measured: from 2,152 to 1,482 MB after eviction).
- **Agent** (`floppy tick`): if the VM is stopped it starts it; if it answers with I/O errors it restarts it, at most once every 10 minutes; if JuiceFS or Samba are stopped it restarts them; it frees the cache if the Mac is nearly full; it mounts the disk if it is missing. It never touches a mounted disk: a disk busy with a large copy answers slowly and looks dead, and a first version force-unmounted it halfway through a copy. After the Mac sleeps, macOS handles the SMB reconnection. `floppy stop` leaves a marker the agent respects; `start` removes it.
- **Samba restarts on its own** (`Restart=on-failure`): measured, a killed `smbd` is back in a few seconds.
- **`status`**: tells apart a VM that is on but broken (every command inside gives an I/O error, while Lima reports it "Running"), a disk that is mounted but not answering, the Mac's free space and the cache ceiling in use.
- **Visible errors**: an unexpected error says where it happened and shows the last lines of `lima.log`. Before, the install could return to the prompt without a word, and that happened on a tester's first attempt.
- **Updating and starting over**: `install.sh` over an existing install updates it (keeps configuration, VM and volume) and says so, whether run from the zip or by the app; starting from scratch is `floppy uninstall` and then `install.sh`. The VM's memory and disk stay as they were at creation.
- **No downloads at install time**: the zip holds the Debian image (the same one Lima's template pins, verified with its SHA-512) and the patched JuiceFS binary (next bullet), and the VM is created from the local file. An office DNS did not resolve `cloud.debian.org`: it was the cause of both of a tester's failed installs. The zip grows from 38 to 406 MB. The Debian packages for Samba and FUSE are in the zip too, as a local repository (94 `.deb`, 38 MB: the whole dependency closure, of which the VM installs only what the image lacks, 18 today), and the zip reaches 445 MB. `build-engine.sh` takes them from a `debian:trixie` container for the `PACKAGES` of `guest-install.sh`, so building the app or the zip needs Docker. If the local repository fails, `guest-install.sh` falls back to `deb.debian.org`. Tested on a VM from the zip's image with `deb.debian.org` blocked, and with a corrupted `.deb` for the fallback.
- **The patched JuiceFS**: the zip carries the build of `spike/juicefs-replica/` (v1.4.1 plus the patches, linux/arm64 like the rest of the package), not the upstream release. Building is part of packaging: `build-engine.sh` runs `build.sh`, which rebuilds only when the patches, the upstream tag or the recipe change (the hash of those inputs is in `dist/BUILD` next to the binaries), then runs the binary in `debian:trixie` and stops unless it reports the `JUICEFS_VERSION` of `guest-install.sh` and the repo's patch set. The version names the patch set, `1.4.1+<date>.<commit>-floppy.<patchset>` with the first 8 hex digits of the SHA-256 of the patches, so `juicefs version` in a VM tells which patches it runs; the suffix sits in the build metadata, which JuiceFS ignores when it compares client versions. Two builds from the same inputs give the same binary (same SHA-256), and the zip stays at 445 MB. With one user (SQLite, no replica) the patches change only the data path (whole-block reads, DECISIONS #15): checked with `floppy-mount` itself in `debian:trixie` against Garage, on a new volume and on one formatted and written by the upstream binary, which is what an update of an existing tester goes through. There is no download fallback: without a binary in the package, `guest-install.sh` keeps an installed one of the same version and otherwise fails, because the upstream release lacks the patches.
- **Updating JuiceFS** (only when a release brings something needed, see [ROADMAP.md](ROADMAP.md)): move the tag in `spike/juicefs-replica/Dockerfile` (`test.sh` reads it from there) and `JUICEFS_VERSION` in `guest-install.sh`; rebase the patches on the new tag (apply each with `git apply --3way` on a clone of the tag, resolve, write it back with `git format-patch`); run `spike/juicefs-replica/test.sh`; `build-engine.sh` then rebuilds on its own, because its inputs changed. `spike/drive/Dockerfile` pins the upstream release for the spike's baseline image and moves with it. A zip already handed out does not update itself: it has to be rebuilt and sent again; the app gets the new engine with a release and `brew upgrade`.
- **Failed installs**: `setup` writes `~/.floppy/installed` only when the install is complete. A VM without that marker is a leftover with no data: `setup` deletes and recreates it, with a new volume name; a healthy VM from before the marker existed is recognized and kept. `install.sh` says "Updating" only if the marker is there.
- **The log does not grow without limit**: the script's messages go to the terminal it started from (fd 3), never to the log, and error handling fires only once; `lima.log` is trimmed past 50 MB and `install.sh` empties it if it is huge. On a tester's Mac the log reached 50 GB and filled the disk: it was confirmed that the error blocks ended up in the log, once per function level; the growth to 50 GB was not reproduced (on that Mac the VM start hung), so the guards hold for anything that writes. The VM start has a 5-minute timeout.
- **Configuration**: `config.env` (template in `macos/config.env.example`) with bucket, keys, the SMB password between Mac and VM, cache and simulated bandwidth limits. The zip holds the keys: share it privately, with an OVH key dedicated to the test bucket, to be revoked at the end of the test.
- **One volume per Mac**: with `VOLUME_NAME` empty, `install.sh` generates `floppy-<user>-<code>`, so the same zip goes to several people on the same bucket, each with their own disk. Reinstalling over an existing install keeps the configuration; `uninstall` deletes everything, because the volume is lost with the VM.
- **Test metadata**: SQLite inside the VM. Uninstalling the package or losing the VM makes the files in the bucket unreadable; several people need the shared metadata (see [Shared metadata over the WAN](#shared-metadata-over-the-wan)).
- **Footprint**: on the Mac the VM takes at most 1 GB of RAM (1,035 MB measured, 670 MB right after start), against 2,060 MB for the first version with 2 GiB. The ceiling is the assigned memory, not JuiceFS's buffer: Linux fills free RAM with the page cache, Apple's framework keeps every touched page and Lima does not use the balloon to give it back, so after the first read of a clip the VM weighs its whole `memory`. Inside the VM JuiceFS reaches 420 MB copying 2.7 GB with `--buffer-size` at 256 MB (it was 1 GB), with no processes killed for running out of memory. Performance stays that of 2 GiB: cold read 11.6-13 MB/s, cold seek 0.8-1 s, warmup of a clip 27-33 s, copying 2.7 GB 129 s (133 s with the previous setup, at the same time). Only rereading after an unmount changes, 0.5 s instead of 0.2, because it comes from the disk cache instead of RAM. On disk the VM grows with the cache. `memory` applies to VMs created from now on: `setup` does not change it on an existing VM, which has to be updated by hand or reinstalled.
- **Measured install**: about 90 s from the zip with image and JuiceFS bundled (84-111 s with the version that downloaded the image). It needs a Mac with Apple Silicon and macOS 13 or later, which `install.sh` checks.
- **Performance**: like the Docker spike at a simulated 100 Mbps: cold read 5.3-7.2 MB/s, cold seek 0.8 s on average. Warm reads are also served by the macOS SMB client's cache.
- **To do**: the Finder Quick Actions ("Make available", "Free up space"); for now they are `floppy warmup <path>` and `floppy free <path>`. Sleep was tested (the disk came back, but the first version of the agent remounted it as `Floppy-1`, fixed). A real restart, a network change and macOS 27 are in a tester's ongoing test, not closed yet ([ROADMAP.md](ROADMAP.md)). The SMB password is to move from the mount URL to the Keychain.

## Alternatives considered

| Alternative | Outcome | Reason |
|---|---|---|
| External Thunderbolt SSD | Ruled out as a team solution | Top speed, but not shared |
| Simple S3 mount (rclone `--vfs-cache-mode full`) | Ruled out | No locks, risk of people overwriting each other; linear prefetch; a mount that hangs after the Mac sleeps |
| Active project on SSD + `rclone sync` to S3 | Ruled out for a team | Robust for one person, does not give a shared disk |
| Office NAS on 10 GbE + sync to S3 | To reconsider | It is the right architecture if the team works in the same office: local performance, simpler to run |
| **SpaceFS Teams** | **Commercial benchmark** | Same architecture, app and support included. Young startup (2.4 M$ pre-seed in August 2026), private beta, macOS 26.4 or later on Apple Silicon, performance not verified by third parties, data region not stated, how it mounts not documented |
| LucidLink | Mature alternative, pricier | Industry standard, and the same design: metadata replicated to every client, data streamed from object storage. Starter 7 $/member with 100 GB, Business 27 $/member with 400 GB, extra space on quote. On the Mac it used macFUSE's kernel extension (2022-2023 sources) |
| LucidLink Custom on one's own bucket | Possible | 32 GBP/TB of license plus storage, about 45 EUR/TB, Enterprise only. OVH is not among the qualified providers |
| Suite Studios | Not evaluated | Sources still contradict each other, on what it is and on the price (40 $/TB plus compute, or about 75 $/TB) |
| Shade | Not evaluated | A video library mounted as a drive, plus AI search and review: a suite more than a disk. 14 M$ in April 2026. One's own S3 bucket only on Enterprise |
| Frame.io Drive (Adobe) | To watch | Frame.io projects mounted in the Finder with streaming and a local cache, out since 15/04/2026 starting from Enterprise plans. Included in Adobe plans, which makes it the alternative a Premiere team already has; no own bucket |
| Storj Object Mount | Not evaluated | Mounts any S3 bucket ("Any S3", 39 $ per license a month): a per-person price, Mac support not confirmed |
| JuiceFS Enterprise, mirror file system | Validates the design | Replicates the metadata to another region so clients there read it locally: the same idea as Floppy's replica, on a proprietary metadata engine |
| [JuiceMount](https://github.com/lelanddutcher/juicemount) | The closest open source project, ideas taken | Apache-2.0, one author, beta (0.4.0, July 2026). JuiceFS behind an NFS server on `localhost` that answers the Finder from an in-memory copy of the metadata, refreshed from Redis every 30 s (other Macs' changes arrive late). Needs macFUSE and an administrator password per session, a NAS as backend, no locks yet, and remounts on its own. Ideas taken into [ROADMAP.md](ROADMAP.md); vetoing `._` files is not one of them (DECISIONS #5) |

## Costs

**S3 storage** (excluding VAT):

| Provider | Space | Egress |
|---|---|---|
| OVH Object Storage Standard | about 8 $/TB per month (third-party source) | free within Europe |
| Backblaze B2 | 6.95 $/TB per month | free up to 3 times the stored data, then 0.01 $/GB |
| Hetzner | 6.49 EUR/month with 1 TB, then 8.70 EUR/TB | 1 TB included, then 1 EUR/TB |
| Cloudflare R2 | 15 $/TB per month | free |

Wasabi is left out: it changed its prices on 01/07/2026 and the new price list was not found.

**Floppy has no per-person price**: self-hosted, a team pays the storage (about 7.5 EUR/TB on OVH) and a fixed 20-30 EUR for the metadata server, the central Valkey and its replica in the datacenter (an estimate: the host is not chosen yet). Adding a person costs nothing; adding a TB costs the storage. The commercial products charge per person.

**Comparison by team size** (estimates, per month):

| Active TB | SpaceFS Teams, 3 people | SpaceFS Teams, 10 people | Floppy, any team size | Floppy's bucket backup on B2 |
|---|---|---|---|---|
| 3 TB | 90 $ | 300 $ | about 40-55 EUR | about 20 EUR |
| 5 TB | 114-138 $ | 300 $ | about 55-70 EUR | about 35 EUR |
| 10 TB | 174-258 $ | 300 $ | about 95-110 EUR | about 70 EUR |

SpaceFS Teams: 30 $/person with 1 TB/person pooled, extra 6-12 $ per 500 GB. The backup column is the bucket replica on a second provider that Floppy needs anyway ([Backup and resilience](#backup-and-resilience)); the commercial prices do not say whether they include one.

The gap widens with every person, but what is left is the cost of running it, which licenses hide: keeping the Valkey up and its backups checked, answering when a Mac does not mount, and carrying the JuiceFS patch across releases (half a day to a day per release that touches it, an estimate). For a small team that time is the real price; it pays off with more people, more TB, or data that must stay on infrastructure the team controls.

## Sources

- [SpaceFS](https://spacefs.com/), [RuntimeWire on SpaceFS](https://runtimewire.com/article/space-streams-cloud-files-directly-into-premiere-photoshop-and-other-mac-apps), [IT Brief UK](https://itbrief.co.uk/story/space-raises-usd-2-4-million-for-ai-native-filesystem)
- [LucidLink, G-Cloud 2026 price list](https://assets.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-15/documents/93417/461052283002657-pricing-document-2026-01-30-1117.pdf), [Cutsio, LucidLink pricing 2026](https://cutsio.com/blog/lucidlink-pricing-2026-plans-costs-hidden-fees), [Backblaze, LucidLink with B2](https://www.backblaze.com/docs/cloud-storage-integrate-lucidlink-with-backblaze-b2)
- [Shade, Suite Studios vs LucidLink](https://shade.inc/blog/suite-studios-vs-lucidlink), [ProVideo Coalition on Suite Studios](https://www.provideocoalition.com/in-depth-suite-studios/)
- [TechCrunch on Shade](https://techcrunch.com/2026/04/22/shade-lands-14m-to-let-creative-teams-search-their-video-libraries-in-plain-english), [Shade pricing](https://shade.inc/pricing), [RedShark on Frame.io Drive](https://www.redsharknews.com/frame-io-drive-mounted-storage-nab-2026), [Storj Object Mount pricing](https://www.storj.io/pricing/change-faqs)
- [Tidbits on LucidLink](https://tidbits.com/2023/03/23/lucidlink-offers-streaming-vision-of-cloud-based-storage/), [Blocks&Files on LucidLink's metadata](https://blocksandfiles.com/2019/05/29/how-lucidlinks-metadata-acceleration-and-parallel-io-gets-faster-cloud-file-access/), [JuiceFS Enterprise mirror file system](https://cf.juicefs.com/en/blog/engineering/metadata-backup-mirror-file-system-resilience-strategies)
- [JuiceMount](https://github.com/lelanddutcher/juicemount), read on 17/10/2026
- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing), [OVH S3 FAQ](https://docs.ovhcloud.com/de/guides/storage-and-backup/object-storage/s3-faq.md), [getdeploying OVH vs Backblaze](https://getdeploying.com/backblaze-vs-ovh), [Hetzner pricing 2026](https://agentdeals.dev/hetzner-pricing-2026), [Wasabi pricing FAQ](https://docs.wasabi.com/docs/may-2026-wasabi-pricing-faqs)
- [JuiceFS Q&A](https://juicefs.com/en/blog/usage-tips/juicefs-24-qas-for-beginners), [Pigsty, JuiceFS on PostgreSQL](https://pigsty.io/docs/juice/)
