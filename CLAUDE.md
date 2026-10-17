# CLAUDE.md

Fluix is a shared remote disk for video teams on macOS: it shows up in the Finder as **Fluix**, and the files live on S3 object storage (OVH) through JuiceFS. On macOS JuiceFS runs in a Lima VM (Debian) and reaches the Mac over SMB, because mounting it directly would need macFUSE's kernel extension. The engine is a bash command (`macos/bin/fluix`) shipped in a private zip. The team architecture (a central Valkey with a metadata replica in each VM, a patched JuiceFS, a control plane, a Tauri app) is drawn in `docs/architecture.html` and the concepts in `docs/concepts/`, proven in `spike/`, not built yet.

Public repository: code, comments, docs and commits in English; README.md is mirrored in README.it.md in the same commit.

## Glossary

The full glossary is in [docs/CONVENTIONS.md](docs/CONVENTIONS.md#glossary). The terms that matter most when changing code:

- **Disk / Fluix**: what the user sees in the Finder. **Volume**: the JuiceFS filesystem behind it, with a name (`VOLUME_NAME`) and its metadata.
- **Metadata**: in the test they live in SQLite **inside the VM** (`/var/lib/fluix/meta.db`): losing the VM makes the data in the bucket unreadable.
- **Cache**: blocks already downloaded, on the VM's disk. **Warmup** is "Make available" (`fluix warmup`), **evict** is "Free up space" (`fluix free`).
- **The user**: someone editing with CapCut, not technical. **Simone**: who develops and decides.

## Commands

```bash
make check                               # bash -n, shellcheck, prose language
scripts/package.sh my-test.env [dist]    # zip for a tester: Lima + command + VM + config (holds the keys); needs Docker running
bash fluix/install.sh                    # from the unpacked package: installs into ~/.fluix and mounts
~/.fluix/bin/fluix status                # setup|start|stop|status|warmup <path>|free <path>|uninstall

# Apply repo changes to this Mac's install (keeps VM and volume)
cp macos/bin/fluix ~/.fluix/bin/ && cp macos/vm/guest/* ~/.fluix/vm/guest/ \
  && ~/.fluix/bin/fluix setup

# Inside the VM
export LIMA_HOME=~/.fluix/vms
~/.fluix/lima/bin/limactl shell --workdir / fluix <cmd>   # e.g. sudo journalctl -u fluix-mount

# Local spike
spike/setup.sh                 # Garage, PostgreSQL, Toxiproxy, drive container; secrets in spike/.env
spike/bench.sh                 # upload, cold/warm reads, seeks, warmup
spike/meta-bench.sh            # metadata from the Mac over SMB: SQLite vs PostgreSQL at 0/15/30 ms
spike/juicefs-replica/build.sh # patched juicefs (read replica, optimistic writes) into dist/
spike/juicefs-replica/test.sh  # the patch's Go tests against Valkey master + replica
JUICEFS_SOURCE=replica docker compose -f spike/docker-compose.yml --profile replica build drive drive-b
spike/acceptance.py [--baseline] [t01 ...]   # two clients, central Valkey + replicas, 13 tests

# Site (Astro + Starlight; the product name lives in site/src/brand.ts)
cd site && pnpm dev            # pnpm build, pnpm check
```

## Gotchas

**Never `juicefs mount` directly on macOS.** With FUSE-T it fails (`no FUSE mount utility found`), with macFUSE's FSKit backend it hangs, and the only working path is the kernel extension, which makes every Mac go through recovery mode. JuiceFS runs in Linux (the VM), the Mac mounts SMB.

**The mount point must stay `/Volumes/Fluix`, but it is never assumed.** CapCut references videos by full path: with `Fluix-1` projects lose their files. macOS picks `-1` when the folder of a previous mount is still there, and it happened after the Mac slept. Mount only on a free name (`mount_share`), and read the mount point from `/sbin/mount` (`mount_point`), never `VOLUME=/Volumes/Fluix`.

**Never unmount or remount a mounted disk automatically.** A disk busy with a large copy answers slowly and looks dead: the agent force-unmounted it halfway through a `cp` and the copy failed. `tick` mounts only if the disk is missing (`mount_share keep`); remounting is only in `fluix start`, asked for by the user, and never with `force`.

**The VM does not see the Mac's disk.** JuiceFS's space check (`--free-space-ratio`) looks at the VM's disk, which believes it is large; on a tester's Mac the cache filled the disk and the VM's filesystem failed. Space limits are computed on the Mac side (`cache_limit_mb`, `RESERVE_MB`, the check in `tick`), and a VM that Lima calls "Running" can be broken inside (`vm_healthy`).

**No script messages in the log, and fd 3 closed for limactl.** Functions often run with redirected output (`lima start >>lima.log`) and the error trap inherits that redirection: it wrote its blocks into `lima.log`, once per function level, and on a tester's Mac the log filled the disk. Messages go to fd 3, the terminal saved at startup. But `lima start` leaves a host agent in the background that inherits fd 3 and keeps the install's terminal open forever: `lima()` closes it (`3>&-`).

**macOS `/bin/bash` is 3.2**: with `set -u` an empty array expanded as `"${a[@]}"` is an error. Write `${a[@]+"${a[@]}"}`.

**No external network during install.** An office DNS did not resolve `cloud.debian.org`: the Debian image, JuiceFS and the Samba and FUSE `.deb` files are in the zip (`scripts/package.sh`). The `.deb` files are the whole dependency closure, taken in a `debian:trixie` container with an empty dpkg status: a plain `--download-only` would miss the libraries the container already has and the image may have in an older version. The list is `PACKAGES` in `guest-install.sh`, which `package.sh` reads; `deb.debian.org` stays as a fallback if the local repository fails.

**`systemctl is-active` with several units says yes if at least one is active.** A check on `fluix-mount smbd` together missed a stopped Samba. One unit per call.

**Absolute paths for system tools.** In some shells and under launchd `/usr/sbin` is not in the PATH: `diskutil` "not found" made a benchmark's remounts fail silently. Write `/usr/sbin/diskutil`, `/usr/bin/osascript`, `/sbin/mount`.

**Do not uninstall or delete the VM to "start clean".** `fluix uninstall` and `limactl delete` throw away the metadata: the data in the bucket stays but can no longer be read, and a reinstall with the same `VOLUME_NAME` on the same bucket collides with the old one. To apply changes, rerun `setup`.

**`setup` does not change the RAM of an existing VM.** `memory` in `lima.yaml` applies only at creation; an existing VM needs `limactl edit --memory <GiB> fluix` while stopped. The rest (`vm/guest/`, config) `setup` reapplies.

**JuiceFS behind Samba needs `--enable-xattr`.** Without it, Samba (`fruit` module, which needs the `samba-vfs-modules` package) cannot store the Finder's metadata and deleting a file from the Mac gives `Operation not supported`.

**JuiceFS does not cache uploaded blocks** unless `--cache-large-write` is on (off by default). The docs assumed it and it was false.

**macOS gives up on an SMB read after 30 s** and the Finder gets an I/O error. The command raises `max_resp_timeout` only for the VM's address, in a `# fluix begin/end` block of `~/Library/Preferences/nsmb.conf`: never rewrite that file whole, it holds the user's configuration.

**Simulate bandwidth with JuiceFS's limits, not Toxiproxy.** Toxiproxy's `bandwidth` is per connection and JuiceFS opens many: 2.7 GB "went through" at 150 MB/s with a 37 MB/s limit. Toxiproxy is only for latency; bandwidth is `--download-limit`/`--upload-limit` (`DOWNLOAD_LIMIT_MBPS`, `UPLOAD_LIMIT_MBPS`).

**Docker ran out of default subnets** on the development Mac (many compose projects): the spike pins an explicit subnet in `spike/docker-compose.yml`. Never `docker network prune`, the networks belong to other projects.

**A UI is verified in a real browser.** The mockup passed checks in node and in the browser drew one variant out of three because of a JavaScript error. Load it in headless Chrome (Playwright with Chrome's `executablePath`) and look at the screenshot.

**Samba multiplies metadata operations.** Listing 300 files from the Finder costs about 2,700 file system operations (lookups of `._` names, extended attributes); with the metadata across a network each is a round trip (117 s at 15 ms). Do not tune Samba or caches around it: reads must come from the local replica (DECISIONS #5).

**macOS mounts a server and share only once.** A second mount of `localhost/Fluix`, even on another port, fails with "File exists": the spike reaches the second client by address (`127.0.0.1`).

**Unmount SMB with `-f` in scripts.** A gentle unmount can leave a stale session, and the next mount of the same share answers I/O errors.

**The spike needs the SMB timeout too.** The real install raises `max_resp_timeout` for the VM's address; the spike talks to `localhost` and `127.0.0.1`, so the same block (`# fluix-spike begin/end` in `~/Library/Preferences/nsmb.conf`) has to be there, or cold reads fail after 30 s.

**The per-Mac Valkey user needs more than reads and writes**: INFO, INCR and DEL on `ryw*`, EVAL and EVALSHA for the optimistic writes, and PSYNC, REPLCONF, PING for its replica. A missing INCR aborts every write transaction.

## Secrets

`spike/.env` (with the PostgreSQL and Valkey passwords), `my-test.env`, `~/.fluix/config.env` and every `dist/*.zip` hold S3 keys and the SMB password: they are outside git (`*.env`, `dist/`) and are never printed, not even in a debug log. To check a file, show the key names, not the values. `package.sh` builds the zip with `umask 077`. OVH S3 keys open every bucket in the project: a tester needs a dedicated user.

## Touch every surface

A change to how the disk behaves almost always goes through several files:

- `macos/bin/fluix` (Mac side) and `macos/vm/guest/` (VM side: `fluix-mount`, `guest-install.sh`, `smb.conf`, the systemd service).
- `macos/vm/lima.yaml` if the VM changes, and then the `setup` gotcha above.
- `macos/config.env.example` if `config.env` gets a new option.
- `macos/install.sh` and `scripts/package.sh` if what goes into the zip changes.
- `docs/ARCHITECTURE.md`: the "Configuration" and "macOS package" sections, and "Measured performance" if a number changes.
- `README.md` and `README.it.md` together.
- A zip already handed out does not update itself: it has to be rebuilt and sent again.

Inverse states: `setup` writes the `nsmb.conf` block and the launchd agent, `uninstall` removes them; `warmup` has `free`; `start` has `stop`. Whoever adds an effect on the user's Mac adds its removal to `uninstall`.

## Never compromise

- **No administrator rights** to use the disk and run the app, and no kernel extension: it is the reason the VM exists. The one exception is installing Homebrew itself, once (DECISIONS #10).
- **SMB only on the private Mac-VM link** (vzNAT address), with Lima's port forwarding off: SMB signing and encryption are off because the traffic never leaves the Mac.
- **No user data deleted without confirmation**: `uninstall` asks to type `delete`, and data in the bucket is touched only on explicit request.

## Precedence

These are default rules for this repo: the global instructions sit below, and what Simone says in chat sits above everything.

## Where to find the rest

- [README.md](README.md): what it is, how to build and hand out the zip.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): why JuiceFS, VM and SMB, configuration, measured performance, alternatives, costs.
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md): language, glossary, code conventions.
- [docs/DECISIONS.md](docs/DECISIONS.md): what was decided and what it ruled out, cited as `DECISIONS #N`.
- [docs/ROADMAP.md](docs/ROADMAP.md): next steps.
- [docs/CYCLES.md](docs/CYCLES.md): what was done and when.
- [docs/architecture.html](docs/architecture.html): the team architecture (control plane, central Valkey with a replica per VM, login) as diagrams and flows; a proposal until it lands in ARCHITECTURE.md.
- [docs/concepts/](docs/concepts/): HTML concepts with variants side by side, one file per design decision.
