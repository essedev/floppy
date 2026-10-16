# Cycles

Log of the work done, most recent first. Plans are in [ROADMAP.md](ROADMAP.md), the reasons behind the choices in [ARCHITECTURE.md](ARCHITECTURE.md).

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
