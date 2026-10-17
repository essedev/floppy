# Cycles

Log of the work done, most recent first. Plans are in [ROADMAP.md](ROADMAP.md), the reasons behind the choices in [ARCHITECTURE.md](ARCHITECTURE.md).

## 9. The team product takes shape (17/10/2026)

- **Shared design system** (`design/tokens.css`, `design/mark/`): the tokens, detail set A and the final marks for every surface; colour lives only in the LED, the slot included.
- **Mac app** (`app/`, Tauri from the Portsage skeleton): the menu bar icon in five states, the panel with the Grafite faceplate, Settings and first run; it reads the engine through `floppy status --json`. Tested against a fake engine only.
- **Admin web** (`web/`): every screen of concept E on a typed contract, with a fake API or the real server; "devices" instead of Macs; pages at full width.
- **Control plane** (`server/`, phases 1 and 2): the schema (DATABASE_SCHEMA.md, DECISIONS #16-#22), sign-in by link, code and invitation, people, devices with one Valkey user each, disks with a real `juicefs format`, key rotation, audit log, rate limits, mail in Italian and English, storage modes with team, per-person and per-device S3 keys and a Garage adapter. 142 tests on real PostgreSQL, Valkey and Garage.
- **JuiceFS patch 0003**: `config` and `format` take the S3 key from the environment. A volume formatted without a key lets each Mac mount with its own key from the environment, so per-person and per-device keys need no mount patch (verified on Garage).
- **Site**: a landing for teams sharing big files, with use cases, written for the finished product; header controls drawn from the faceplate; docs pages aligned; Starlight kept for the docs only.
- **Architecture page**: the target architecture only, a plain-words explanation of blocks and caching, and a section on the JuiceFS patches.
- **Name**: Floppy (DECISIONS #14); no live FLOPPY mark for software in the EU; Italian marks from the 1990s still to check on UIBM.
- **In progress**: per-disk permissions (none, read, read-write) with one Valkey instance and one bucket per disk, being verified in the spike before the decisions and the code change.

## 8. The data path (17/10/2026)

- **Bench and link** (`spike/data-bench.sh`, the `shaped` profile): cold reads inside the Linux box and from the Mac, seeks, scrubbing. JuiceFS's own download limit, used so far to simulate the link, lets a megabyte through and then stalls: a seek took 34 ms alone and 390 ms right after another. The link is now a token bucket on Toxiproxy.
- **Causes found**: a cold read downloads the whole block (4 MB); after a jump the readahead of the abandoned position competes with the new one; through its cache the macOS SMB client sends 128 KB pieces out of order and up to 40 MB ahead, and JuiceFS turned them into range requests and downloaded blocks twice.
- **Choices** (DECISIONS #15): 1 MB blocks, 4 MB readahead, a second JuiceFS patch that reads whole blocks inside a sequential stream, with its Go test. In `floppy-mount` for new volumes; the patched JuiceFS reaches the product with the build (ROADMAP).
- **Results at 100 Mbps**: cold reads over SMB 86-93% of the line (target 80%), a cold seek 0.38 s (target 0.4), the first read after a jump 0.40 s, no read over 1.5 s. The acceptance suite passes 17 of 17, `t03` included. At 1 Gbps: 82% of the line, seeks 0.09 s.
- **Not needed**: SMB multichannel. **Still to confirm**: the same numbers on OVH Milan.

## 7. The name: Floppy (17/10/2026)

- **Name chosen** (DECISIONS #14): no active "Floppy" in storage, sync or video, and the only relevant US mark (FLOPPY, Hyperfoo, Inc.) is cancelled. EUIPO and UIBM are still to check; domains are set aside.
- **Renamed from Fluix to Floppy** in one pass: the command (`macos/bin/floppy`), the disk and its mount point `/Volumes/Floppy`, the VM, the `floppy-mount` service, `~/.floppy` and `/var/lib/floppy`, the launchd agent `dev.esse.floppy`, the `# floppy begin/end` block in `nsmb.conf`, the spike (bucket, PostgreSQL user, containers and Docker volumes), the docs, the concepts and the site. The rejected "Floppy" visual direction in the identity concept is now "Diskette". The entries below keep the names of their time.
- **Existing installs keep the old name**: there is no migration code. An install made from an earlier zip is removed with its own `~/.fluix/bin/fluix uninstall`; a new zip installs Floppy beside it. The spike's state from before the rename (the PostgreSQL user, the bucket, `~/.fluix-spike`) does not carry over either.

## 6. Shared metadata that stays fast over the WAN (17/10/2026)

- **Step 0 measured** (`spike/meta-bench.sh`, `spike/meta-access.sh`): with the metadata in PostgreSQL behind a simulated 15 ms link, creating 450 files from the Finder took 820 s and listing 300 files 117 s, against 14 s and 0.24 s with SQLite in the VM. Inside the VM, without Samba, the same listing took 0.12 s: Samba asks JuiceFS about 2,700 questions for it (lookups of `._` names, extended attributes), each a round trip.
- **Architecture chosen** after comparing alternatives (DECISIONS #5): central Valkey with a replica in each VM, JuiceFS patched to read from it (`spike/juicefs-replica`, on v1.4.1), writes as optimistic transactions at one round trip (DECISIONS #6), offline as read-only.
- **Acceptance suite** (`spike/acceptance.py`): two clients, the Mac over SMB, 17 checks; 16 pass with the third round of the patch. Cold listing of 300 files 0.29 s (74.6 s without the replica), a new file visible on the other Mac in 0.01 s, 100 small files in 14.2 s (158 s), offline browsing works and writes fail in 0.1 s, the link back in 0.9 s (34 s), a slow master (1.2 s +/- 0.6 s) never turns the disk read-only, read-your-writes, concurrent renames, revocation. The one failure is a cold read over SMB that sometimes ends in an I/O error, also with local metadata: it moves to the data-path spike, with SMB capping cold reads at about half the line.
- **The patch in three rounds**: reads from the local replica with read-your-writes; writes as optimistic transactions at one round trip; offline only on unambiguous unreachability or 10 s without any answer from the master. A latent bug fixed on the way: after a reformat, a lagging replica could look up to date.
- **Found while testing**: macOS refuses a second SMB mount of the same server and share even on another port; a gentle unmount can leave a stale SMB session that answers I/O errors; the spike needs the SMB timeout raised for `localhost` too.

## 5. Team architecture, identity and the app's design (16-17/10/2026)

- **Team architecture**: login in the browser, a control plane off the data path, the same code self-hosted or hosted, MIT for the app and AGPL-3.0 for the control plane, only standard S3 and database protocols (DECISIONS #2-#4). Drawn in [architecture.html](architecture.html).
- **Identity**: four directions compared ([concept-b-identity](concepts/concept-b-identity.html)); chosen the drive with the Grafite faceplate and the Essenziale icons, and a vocabulary of details taken from the object (DECISIONS #11).
- **Concepts of every surface**: the app panel, settings and notifications ([concept-c-app](concepts/concept-c-app.html), dynamic on the choices), first run and sign-in ([concept-d-onboarding](concepts/concept-d-onboarding.html)), the admin web ([concept-e-admin](concepts/concept-e-admin.html)); every open decision set (DECISIONS #12).
- **Tauri** confirmed over Swift, **Homebrew** as the only channel, **Astro and Starlight** for the site (DECISIONS #9, #10, #13).
- **Name**: "Fluix" collides with Fluix Limited in software; a new name is being chosen (DECISIONS #14).
- **The tester's run** on the 08/10 zip ended well.

## 4. Offline install and open source (16/10/2026)

- **Debian packages in the zip**: Samba, FUSE and their whole dependency closure (94 `.deb`, 38 MB) ship as a local apt repository, fetched by `package.sh` from a `debian:trixie` container with an empty dpkg status. The install no longer reaches any external host; `deb.debian.org` stays as a fallback. Tested on a VM from the zip's image with `deb.debian.org` blocked (18 packages installed from the zip) and with a corrupted `.deb` (fallback taken, same 18 packages).
- **Renamed to Fluix** and prepared as a public repository: command, disk, VM, services and data folder carry the new name, the documentation is in English with an Italian README, MIT license. The git history was rewritten to drop the old name and private references.
- **`make check`**: `bash -n`, `shellcheck` and the prose language check on every script; shellcheck found an unguarded `rm -rf` in `install.sh`, fixed.

## 3. A tester's second install: DNS and log (08/10/2026)

- **What happened**: creating the VM failed because the office DNS answers SERVFAIL for `cloud.debian.org` (already the cause of the silent failure on 6/10). On the rerun, `install.sh` said "Updating" on a VM that never finished, the start hung and `lima.log` went past 50 GB, filling the disk. The tester's diagnosis, verified: the error blocks ended up in the log. The 9.7 GB copy on 6/10 had run at 14 MB/s, the tester's upload.
- **Fixes**: Debian image and JuiceFS inside the zip (406 MB), VM created from the local file (verified in Lima's log); a completed-install marker, with a half-made VM deleted and recreated; messages only on the terminal, a trap that fires once, the log trimmed past 50 MB, a 5-minute start timeout.
- **Tested**: the tester's state reproduced with the real Lima and an unreachable image (half-made VM recognized and recreated, log without error blocks); a clean install from the new zip in about 90 s, a 35 GiB VM disk.
- **Found while testing**: fd 3, inherited by Lima's host agent, kept the install's terminal open; closed for limactl.
- **Delivery**: zip rebuilt with `CACHE_SIZE_MB=15360` (cache up to 15 GB, VM up to about 25 GB on the Mac) and handed to the tester, who reinstalled; the test is in progress ([ROADMAP.md](ROADMAP.md)). The orphan volumes of the failed installs were deleted from the bucket.

## 2. A tester's first run and the space guards (06/10/2026)

- **Incident**: on the tester's Mac (macOS 27.0.1, 16 GB of RAM, 10 GB free out of 228) uploading 9.9 GB and using CapCut filled the Mac's disk. The VM's file could not grow any more, the VM's filesystem failed, CapCut saw "disconnected from server" and every command in the VM answered `Input/output error`. The 9.9 GB were already all on OVH. The first install had also failed silently.
- **Cause**: JuiceFS's space check looks at the VM, not the Mac.
- **Fixes**: a cache ceiling computed from the Mac's space and a VM disk sized on that ceiling; an agent every 2 minutes that restarts the VM and services, frees the cache when the Mac is nearly full and mounts the disk if it is missing; `fstrim` after `free`; a `status` that sees a broken VM; errors that show the log; an explicit update in `install.sh`.
- **Found while testing the fixes**: after the Mac slept the agent remounted the disk as `Fluix-1`, which breaks CapCut projects; an impatient agent force-unmounted a disk busy with a copy; `systemctl is-active` on two units missed a stopped Samba. Fixed and retested: 3 clips copied with 5 agent checks in parallel, identical byte for byte.
- **For the tester**: a clean reinstall after freeing space; the orphan volume to delete from the bucket (done in cycle 3).

## 1. From the analysis to the test package (06/10/2026)

**Goal**: find out whether a remote disk on S3 holds up for video editing on a Mac, and get to something a tester can try.

**Done**:

- **Analysis**: the streaming filesystem category (LucidLink, SpaceFS), the choice of JuiceFS on OVH, costs and alternatives ([ARCHITECTURE.md](ARCHITECTURE.md)).
- **Local spike** (`spike/`): Garage in Docker as S3, latency and bandwidth simulated with Toxiproxy, JuiceFS in a Linux container shared to the Mac over SMB. JuiceFS directly on the Mac needs macFUSE's kernel extension (FUSE-T and FSKit are not enough), the Linux machine with SMB does not.
- **macOS package** (`macos/`, `scripts/package.sh`): a Lima VM with the `vz` backend, the `fluix` command, a zip with `install.sh`, no administrator rights. One volume per Mac generated at install time, so the same zip goes to several people.
- **Test on OVH Milan**: a test bucket with a dedicated user. Uncached playback at 12 MB/s on a 170 Mbps home line, cold seeks around a second, cached like a local disk. Fixed along the way: the `Fluix-1` mount point and caching of uploaded files (`--cache-large-write`).
- **VM RAM**: from 2,060 to 1,035 MB on the Mac, with `memory` at 1 GiB and `--buffer-size` at 256 MB, with the same performance.
- **Panel mockup** for the menu bar (`docs/concepts/concept-a-panel.html`), three variants A, B and C.

**Decisions**: a Linux machine with SMB instead of macFUSE; macOS only with the system layer isolated; a private zip instead of Homebrew for the test; a Tauri app from Portsage instead of Swift, with a first version limited to what the command does. Reasons in [ARCHITECTURE.md](ARCHITECTURE.md).

**Next step**: the test on a tester's Mac ([ROADMAP.md](ROADMAP.md)).
