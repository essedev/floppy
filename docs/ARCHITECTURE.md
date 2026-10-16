# Architecture

Fluix is a shared remote disk for teams that edit video: on a Mac it shows up as a volume in the Finder, the files live on S3 object storage, and the people working on it need to know nothing else. This document collects the analysis so far and the decisions that came out of it. The project is in testing: there is a test package for macOS on an OVH bucket in Milan, with the metadata inside each Mac, no shared PostgreSQL and no app.

Prices and features of third-party services were checked on 06/10/2026 and must be checked again before deciding.

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

JuiceFS (Community Edition, open source) has the same architecture as LucidLink: data is stored as blocks on S3, metadata (names, folders, sizes) in a shared database, and every Mac keeps a local cache.

| Component | Choice | Role |
|---|---|---|
| Data | OVH Object Storage Standard bucket, versioning to be decided (see below) | The file blocks |
| Metadata | PostgreSQL, preferably managed by OVH, in the same region | File and folder tree, locks |
| Client | `juicefs` in a small Linux machine on each Mac, shared to the Mac over SMB on the private Mac-VM link | The volume in the Finder and the cache |

**Why a Linux machine and not `juicefs` directly on the Mac**: see [macFUSE](#macfuse). The Mac mounts an SMB network folder, which it can do on its own without extensions; JuiceFS runs in Linux, where FUSE is native. The price is one more component on each Mac.

**Region**: data and metadata as close as possible to the people editing. Every file and folder operation is a round trip to the database: at 1-3 ms the disk feels local, at 30 ms it does not. Object Storage exists in Milan (region `EU-SOUTH-MIL`, 3-AZ): the test bucket lives there and answers in about 15 ms. Still to check: managed PostgreSQL in Milan, and whether a cheaper 1-AZ region is enough for production.

**Why OVH**: about 8 $/TB per month and free egress within Europe. For video, egress matters more than space, because every clip that is not cached is downloaded again.

**Why managed PostgreSQL**: it is the critical component (see Backup), and a managed one brings backups and PITR without a server to look after, in line with the guiding principle. The price is to be checked; the alternative is a VPS at about 15-30 EUR/month.

**Versioning and Object Lock**: off on the test bucket. JuiceFS has its own trash; with versioning every block JuiceFS deletes or compacts would stay in the bucket and be billed, and Object Lock would stop it from deleting and compacting. Against deletions there are the trash and the replica (see Backup). For production it is an open decision.

## The cache

The cache lives on each Mac's SSD, inside the Linux machine's disk, managed by the JuiceFS client. It is what makes the disk fast.

- JuiceFS splits every file into 4 MB blocks. When a program reads a point of a video, only the blocks of that point are downloaded, and a copy stays in the cache. Next time they come from the SSD.
- **How blocks get in**: by reading, with prefetch downloading ahead during playback; with `juicefs warmup <folder>`, which preloads a whole project; and by writing, but only with `--cache-large-write`, which is off by default: whoever uploads the footage has it cached, the others do not.
- **They survive a restart**, until: the cache reaches `--cache-size` (the least recently used blocks go), free space drops below `--free-space-ratio` (default 10%), `--cache-expire` runs out if set, or they are freed by hand with `juicefs warmup --evict <folder>`. The blocks of a deleted file disappear at once on the Mac that deletes it; on the others they become orphans and are the first to go.
- **`--free-space-ratio` does not protect the Mac**: it looks at the Linux machine's disk, not the Mac's. In the package the cache ceiling and the emergency eviction are computed on the Mac side (see [macOS package](#macos-package)).
- **Every Mac has its own cache**: what one person has watched does not help the others.
- **It does not work offline**: even with the blocks cached, the file tree lives in the database. Without a network the disk cannot be used. The exception is the test setup, with the metadata in SQLite inside the VM: there the cached files should stay readable, but this is not tested.
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
- **Scrubbing a clip never opened is slow**: almost a second per seek. Warmup removes it (3 ms). The next parameter to try is a block smaller than 4 MB, which makes a cold seek cheaper at the cost of more requests.
- SMB still has a cost: the line gives about 21 MB/s, the Mac gets 12 cold.

**Local spike** (simulation, before OVH):

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
- **SMB costs half the cold bandwidth**: inside the Linux machine JuiceFS reads at 10.7 MB/s on 100 Mbps, over SMB the Mac gets 6.7. The macOS SMB client keeps few reads in flight; a higher prefetch helps little (3: 5 MB/s, 8: 6.7 MB/s, 16: 6.3 MB/s).
- **A cold seek pays for the whole block**: reading 1 MB at a new point downloads the 4 MB block that holds it.
- Local Garage in Docker is not OVH: the measures isolate latency and bandwidth, not the real service's performance. They were repeated on OVH (above).
- The first opening of a folder with many videos is slow, because the Finder and CapCut read a piece of every file for the previews. Not measured yet.

## Configuration

| Setting | Value | Reason |
|---|---|---|
| Compression | off | Video is already compressed |
| Block size | 4 MB (default) | Balances sequential reads and seeks |
| Trash | 7 days | Recovery from accidental deletions |
| `writeback` | off | A file counts as saved only once it is on S3. Slower copies, but no file left only on a Mac closed halfway through an upload |
| `--cache-size` | computed at every `setup`, at most `CACHE_SIZE_MB` (100 GB by default) | Half of what the Mac can spare above a reserve (see [macOS package](#macos-package)) |
| `--cache-expire` | 30 days | Old projects free themselves |
| `--cache-large-write` | on | Whoever uploads footage usually edits it: uploaded blocks stay cached. By default JuiceFS does not keep them |
| `--prefetch` | 8 | Measured: the best value cold over SMB |
| `--buffer-size` | 256 MB | Measured: as fast as 1 GB and leaves room in a 1 GiB VM (see "Footprint") |
| `--enable-xattr` | on | Samba stores the Finder's metadata there; without it, deleting from the Mac fails with "Operation not supported" |
| Metadata cache | JuiceFS defaults | Longer timeouts, to make fewer database round trips from the Finder, to be evaluated with shared PostgreSQL: with SQLite in the VM the round trip is local |
| Samba | `fruit` module (with `samba-vfs-modules`), signing and encryption off, asynchronous I/O | Finder compatibility; signing off and asynchronous I/O take cached reads from 69 to 163 MB/s. Off only because SMB stays on the private Mac-VM link |
| Samba, hidden files | `veto files` on `.accesslog`, `.config`, `.stats`, `.trash` | JuiceFS's internal files do not show up in the Finder |
| Spotlight | `mdutil -i off` on the volume | No indexing that reads everything |

## Working with CapCut

- **Media live on Fluix, CapCut drafts stay on the Mac**: CapCut saves projects in its local folder and references videos by the path they are at.
- **A draft has a single owner**: two people cannot open the same draft. To hand a project over, use CapCut's cloud space or an export.
- **One folder per project**, holding the footage and the exports. No further structure until the pilot shows a need for it.
- **To check in the pilot**: how CapCut reacts to a video that is slow to arrive. If it errors out or loses the link to the file, warming up before editing becomes mandatory.

## Interface

JuiceFS Community has no end-user interface at all: it is a terminal program. People editing see only a volume in the Finder.

**Phase 1, pilot**: the Finder plus two Quick Actions in the right-click menu, "Make available" (`juicefs warmup`) and "Free up space" (`juicefs warmup --evict`), run inside the Linux machine, and the SMB folder mounted at login.

**Phase 2, only if the pilot justifies it**: a menu bar app. It is the part commercial products charge for. The candidate features, each tied to a problem to observe:

| Problem | Feature |
|---|---|
| The disk does not show up after sleep or a network change | Mount at login, connected/not connected status, Reconnect |
| Can I close the Mac? | Files being uploaded and a warning before shutdown (only needed with `writeback` on) |
| This clip stutters | How much of a project is already on the Mac, "Make available" with progress |
| The Mac is full | Space used by the cache, freeing per project |
| Plain-text credentials on every Mac | Login with SSO and personal, revocable credentials |
| It does not work and nobody knows why | "Send diagnostics" with logs and status |

Planned form: a **Tauri app** in the menu bar, built on the skeleton of [Portsage](https://github.com/essedev/portsage), which already has a menu bar icon and panel, launch at login and an external binary bundled in the app (`externalBin`). Swift was ruled out: it would only be needed for native Finder badges and menus, which a Swift extension could add next to the app without rewriting it.

**Early first version, command only.** Work on the app starts before the pilot ends, but the first version does only what the `fluix` command already does (status, turn on and off, make available, free up space, space used on the Mac): that is the engine the app drives, so it is not wasted work. The features in the table above stay tied to what the pilot shows.

**Panel mockup**: `mockups/panel.html`, three variants side by side with the Normal, Downloading, Unreachable, Starting and Off states, and buttons that simulate a download at the speed measured on OVH. Style and colors are Portsage's. The variant is still to be chosen:

- **A, Projects first**: the list of projects with how much is already on the Mac and the action to change it; the disk status is a label that expands only when something is wrong. Same skeleton as Portsage's panel.
- **B, Status first**: one sentence and a large button that does the right thing in the current state (Open in Finder, Reconnect, Turn on); below, the Mac's space split by project, with the actions in each row's menu.
- **C, Compact, like the Finder**: the disk is an entry with Eject, the projects a list with iCloud Drive's status icons; selecting a row shows details and actions.

Decisions the mockup leaves open:

- **Text when the disk is unreachable**: the mockup says files will not open until the disk comes back. That holds with shared PostgreSQL, not entirely with the metadata in the VM (see [The cache](#the-cache)). It is chosen with the final setup.
- **Full cache**: the mockup blocks a project that does not fit with "Space needed". The alternative is to warn and let JuiceFS free the least recently used blocks.
- **Automatic reconnection**: the panel promises a new attempt every 30 seconds; today the agent remounts a missing disk every 2 minutes (see "Agent").
- **"Turn off" or "Eject"**: the variants use different words for the same action; the app uses one.
- Project names, version and dates are made up.

**macOS only for now, with the system layer isolated.** The interface (status, cache, warmup) is shared; only how JuiceFS runs changes, behind a single interface (mount, unmount, status, warmup, free up space) with one module per system selected by `#[cfg(target_os)]`, like Portsage's scanner:

| System | JuiceFS | Linux machine |
|---|---|---|
| macOS | in a light Linux VM, shared over SMB | yes, the only way without a kernel extension |
| Windows | native `juicefs.exe` with WinFsp, a drive letter | no |
| Linux | native with FUSE | no |

Only the macOS module is built; Windows is added as a module, not a rewrite, when needed. At that point the maturity of JuiceFS's Windows client and a distribution channel other than Homebrew (installer or winget) need checking.

**Distribution with Homebrew**, like Portsage: a cask in its own tap, without an Apple Developer ID, with `xattr -cr` in postflight. Limits: Homebrew has to be installed on every Mac, updates need `brew upgrade`, and the trick for unsigned apps depends on Homebrew's rules, which are getting stricter. Login with SSO comes later.

Estimated effort, a guess: 3-4 days for status, mounting, warmup and cache starting from Portsage, plus managing the Linux machine. Maintenance at every macOS or JuiceFS update.

## Access and security

- Every Mac holds the database and bucket credentials. **There are no per-person permissions**: whoever has the disk sees and can delete everything. Trash and replica mitigate deletions.
- **OVH S3 keys open every bucket in the Public Cloud project**, not just their own. The test user (Object Storage Operator role) sees only the test bucket today because the project has no other; if others arrive, the user needs a policy limited to its bucket.
- **Someone leaving**: without the app, the credentials have to be rotated on every Mac. With the phase 2 app, personal credentials (one PostgreSQL role and one S3 key per person) are revoked one by one. Permissions stay "all or nothing" anyway.
- If per-user or per-folder control is needed, JuiceFS Community does not offer it simply; SpaceFS and LucidLink do.

## Backup and resilience

- **The metadata database is the critical point**: lose it and the filesystem is lost, because the blocks on S3 alone cannot be put back together into files. PITR is needed, plus a daily `juicefs dump` kept outside the database.
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

The macOS module, a command today (`macos/bin/fluix`) and the engine of the Tauri app tomorrow. It ships as a zip built by `scripts/package.sh <config.env>`, without Homebrew; whoever receives it runs `bash install.sh` from the Terminal.

- **Lima 2.2.1 VM**, official binary bundled in the zip and verified with the release checksum. `vz` backend (Apple's Virtualization framework), Debian 13, 2 CPUs, 1 GiB of RAM, a sparse disk as large as the cache ceiling plus 10 GiB, fixed at creation. No administrator rights: everything lives in `~/.fluix`, with the VMs in their own `LIMA_HOME` that does not touch other Lima installs.
- **`vzNAT` network**: the VM has an address on the Mac (e.g. `192.168.64.2`) and SMB goes there directly, without the port forwarding that cost bandwidth in the Docker spike. Lima's automatic forwarding is off (`portForwards` with `ignore`), so the VM exposes no ports on the Mac.
- **Inside the VM**: `vm/guest/guest-install.sh` installs Samba and JuiceFS and two systemd services, the mount (`fluix-mount`, which formats the volume on first start) and `smbd`, which starts only once the mount is ready. It can be rerun safely to update.
- **On the Mac**: the volume is mounted with `osascript` (`mount volume`) and shows up in `/Volumes/Fluix` and in the Finder sidebar; Spotlight is off on the volume; a launchd agent (`dev.esse.fluix`) runs `fluix tick` at login and every 2 minutes (see "Agent").
- **Mount point**: it must stay `/Volumes/Fluix`, because CapCut projects reference videos by full path and with `Fluix-1` the links break. macOS picks `-1` when the folder of a previous mount still exists: the command waits for it to disappear before mounting, and logs a warning if the name changes anyway. Commands still read the mount point from `mount`.
- **SMB timeout**: macOS fails an SMB read after 30 s without an answer. The command raises the limit to 600 s only for the VM's address, in its own block inside `~/Library/Preferences/nsmb.conf` (per user, no administrator). An I/O error showed up once at a simulated 100 Mbps, with a cold read started right after a 2.7 GB upload; it did not come back in the A/B comparison, and the limit stays as a guard for slow lines.
- **Space on the Mac** (incident of 06/10, see [CYCLES.md](CYCLES.md)): the VM does not see the Mac's disk, and JuiceFS's space check looks only at the VM's. On a tester's Mac with 10 GB free the cache filled the disk, the VM's file could not grow any more and its filesystem failed. Now `setup` computes the cache ceiling at every run: half of what the Mac can spare above `RESERVE_MB` (20 GB by default), counting the space the cache already takes as spare, at most `CACHE_SIZE_MB`. Below 5 GB `setup` refuses. The agent evicts the whole cache when the Mac drops below half of the reserve, with a notification.
- **Space given back**: the VM's disk file grows but does not shrink on its own; freed space goes back to the Mac with `fstrim`, which Debian runs weekly. `fluix free` and the emergency eviction run it at once (measured: from 2,152 to 1,482 MB after eviction).
- **Agent** (`fluix tick`): if the VM is stopped it starts it; if it answers with I/O errors it restarts it, at most once every 10 minutes; if JuiceFS or Samba are stopped it restarts them; it frees the cache if the Mac is nearly full; it mounts the disk if it is missing. It never touches a mounted disk: a disk busy with a large copy answers slowly and looks dead, and a first version force-unmounted it halfway through a copy. After the Mac sleeps, macOS handles the SMB reconnection. `fluix stop` leaves a marker the agent respects; `start` removes it.
- **Samba restarts on its own** (`Restart=on-failure`): measured, a killed `smbd` is back in a few seconds.
- **`status`**: tells apart a VM that is on but broken (every command inside gives an I/O error, while Lima reports it "Running"), a disk that is mounted but not answering, the Mac's free space and the cache ceiling in use.
- **Visible errors**: an unexpected error says where it happened and shows the last lines of `lima.log`. Before, the install could return to the prompt without a word, and that happened on a tester's first attempt.
- **Updating and starting over**: `install.sh` over an existing install updates it (keeps configuration, VM and volume) and says so; starting from scratch is `fluix uninstall` and then `install.sh`. The VM's memory and disk stay as they were at creation.
- **No downloads at install time**: the zip holds the Debian image (the same one Lima's template pins, verified with its SHA-512) and the JuiceFS binary (verified with the release checksum), and the VM is created from the local file. An office DNS did not resolve `cloud.debian.org`: it was the cause of both of a tester's failed installs. The zip grows from 38 to 406 MB. The Debian packages for Samba and FUSE are in the zip too, as a local repository (94 `.deb`, 38 MB: the whole dependency closure, of which the VM installs only what the image lacks, 18 today), and the zip reaches 445 MB. `package.sh` takes them from a `debian:trixie` container, so building the zip needs Docker. If the local repository fails, `guest-install.sh` falls back to `deb.debian.org`. Tested on a VM from the zip's image with `deb.debian.org` blocked, and with a corrupted `.deb` for the fallback.
- **Failed installs**: `setup` writes `~/.fluix/installed` only when the install is complete. A VM without that marker is a leftover with no data: `setup` deletes and recreates it, with a new volume name; a healthy VM from before the marker existed is recognized and kept. `install.sh` says "Updating" only if the marker is there.
- **The log does not grow without limit**: the script's messages go to the terminal it started from (fd 3), never to the log, and error handling fires only once; `lima.log` is trimmed past 50 MB and `install.sh` empties it if it is huge. On a tester's Mac the log reached 50 GB and filled the disk: it was confirmed that the error blocks ended up in the log, once per function level; the growth to 50 GB was not reproduced (on that Mac the VM start hung), so the guards hold for anything that writes. The VM start has a 5-minute timeout.
- **Configuration**: `config.env` (template in `macos/config.env.example`) with bucket, keys, the SMB password between Mac and VM, cache and simulated bandwidth limits. The zip holds the keys: share it privately, with an OVH key dedicated to the test bucket, to be revoked at the end of the test.
- **One volume per Mac**: with `VOLUME_NAME` empty, `install.sh` generates `fluix-<user>-<code>`, so the same zip goes to several people on the same bucket, each with their own disk. Reinstalling over an existing install keeps the configuration; `uninstall` deletes everything, because the volume is lost with the VM.
- **Test metadata**: SQLite inside the VM. Uninstalling the package or losing the VM makes the files in the bucket unreadable; several people need PostgreSQL (`META_URL`).
- **Footprint**: on the Mac the VM takes at most 1 GB of RAM (1,035 MB measured, 670 MB right after start), against 2,060 MB for the first version with 2 GiB. The ceiling is the assigned memory, not JuiceFS's buffer: Linux fills free RAM with the page cache, Apple's framework keeps every touched page and Lima does not use the balloon to give it back, so after the first read of a clip the VM weighs its whole `memory`. Inside the VM JuiceFS reaches 420 MB copying 2.7 GB with `--buffer-size` at 256 MB (it was 1 GB), with no processes killed for running out of memory. Performance stays that of 2 GiB: cold read 11.6-13 MB/s, cold seek 0.8-1 s, warmup of a clip 27-33 s, copying 2.7 GB 129 s (133 s with the previous setup, at the same time). Only rereading after an unmount changes, 0.5 s instead of 0.2, because it comes from the disk cache instead of RAM. On disk the VM grows with the cache. `memory` applies to VMs created from now on: `setup` does not change it on an existing VM, which has to be updated by hand or reinstalled.
- **Measured install**: about 90 s from the zip with image and JuiceFS bundled (84-111 s with the version that downloaded the image). It needs a Mac with Apple Silicon and macOS 13 or later, which `install.sh` checks.
- **Performance**: like the Docker spike at a simulated 100 Mbps: cold read 5.3-7.2 MB/s, cold seek 0.8 s on average. Warm reads are also served by the macOS SMB client's cache.
- **To do**: the Finder Quick Actions ("Make available", "Free up space"); for now they are `fluix warmup <path>` and `fluix free <path>`. Sleep was tested (the disk came back, but the first version of the agent remounted it as `Fluix-1`, fixed). A real restart, a network change and macOS 27 are in a tester's ongoing test, not closed yet ([ROADMAP.md](ROADMAP.md)). The SMB password is to move from the mount URL to the Keychain.

## Alternatives considered

| Alternative | Outcome | Reason |
|---|---|---|
| External Thunderbolt SSD | Ruled out as a team solution | Top speed, but not shared |
| Simple S3 mount (rclone `--vfs-cache-mode full`) | Ruled out | No locks, risk of people overwriting each other; linear prefetch; a mount that hangs after the Mac sleeps |
| Active project on SSD + `rclone sync` to S3 | Ruled out for a team | Robust for one person, does not give a shared disk |
| Office NAS on 10 GbE + sync to S3 | To reconsider | It is the right architecture if the team works in the same office: local performance, simpler to run |
| **SpaceFS Teams** | **Commercial benchmark** | Same architecture, app and support included. Young startup (2.4 M$ pre-seed in August 2026), performance not verified by third parties, data region not stated |
| LucidLink | Mature alternative, pricier | Industry standard. Starter 7 $/member with 100 GB, Business 27 $/member with 400 GB, extra space on quote |
| LucidLink Custom on one's own bucket | Possible | 32 GBP/TB of license plus storage, about 45 EUR/TB. OVH is not among the qualified providers |
| Suite Studios | Not evaluated | Sources contradict each other on what it is (mounted filesystem or cloud workstation) |

## Costs

**S3 storage** (excluding VAT):

| Provider | Space | Egress |
|---|---|---|
| OVH Object Storage Standard | about 8 $/TB per month (third-party source) | free within Europe |
| Backblaze B2 | 6.95 $/TB per month | free up to 3 times the stored data, then 0.01 $/GB |
| Hetzner | 6.49 EUR/month with 1 TB, then 8.70 EUR/TB | 1 TB included, then 1 EUR/TB |
| Cloudflare R2 | 15 $/TB per month | free |

Wasabi is left out: it changed its prices on 01/07/2026 and the new price list was not found.

**Comparison for a team of 3** (estimates):

| Active TB | SpaceFS Teams (30 $/person, 1 TB/person pooled, extra 6-12 $ per 500 GB) | Fluix (OVH storage + database) |
|---|---|---|
| 3 TB | 90 $ | about 40-55 EUR |
| 5 TB | 114-138 $ | about 55-70 EUR |
| 10 TB | 174-258 $ | about 95-110 EUR |

Doing it yourself costs about half, but the difference (about 500-1,800 EUR a year with 3-5 people and a few TB) has to be weighed against the time for setup, running it and, if needed, building the app. Fluix pays off with more people or more TB, or when the data must stay on infrastructure you control.

## Sources

- [SpaceFS](https://spacefs.com/), [RuntimeWire on SpaceFS](https://runtimewire.com/article/space-streams-cloud-files-directly-into-premiere-photoshop-and-other-mac-apps), [IT Brief UK](https://itbrief.co.uk/story/space-raises-usd-2-4-million-for-ai-native-filesystem)
- [LucidLink, G-Cloud 2026 price list](https://assets.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-15/documents/93417/461052283002657-pricing-document-2026-01-30-1117.pdf), [Cutsio, LucidLink pricing 2026](https://cutsio.com/blog/lucidlink-pricing-2026-plans-costs-hidden-fees), [Backblaze, LucidLink with B2](https://www.backblaze.com/docs/cloud-storage-integrate-lucidlink-with-backblaze-b2)
- [Shade, Suite Studios vs LucidLink](https://shade.inc/blog/suite-studios-vs-lucidlink)
- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing), [OVH S3 FAQ](https://docs.ovhcloud.com/de/guides/storage-and-backup/object-storage/s3-faq.md), [getdeploying OVH vs Backblaze](https://getdeploying.com/backblaze-vs-ovh), [Hetzner pricing 2026](https://agentdeals.dev/hetzner-pricing-2026), [Wasabi pricing FAQ](https://docs.wasabi.com/docs/may-2026-wasabi-pricing-faqs)
- [JuiceFS Q&A](https://juicefs.com/en/blog/usage-tips/juicefs-24-qas-for-beginners), [Pigsty, JuiceFS on PostgreSQL](https://pigsty.io/docs/juice/)
