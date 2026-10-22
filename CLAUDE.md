# CLAUDE.md

Floppy is a shared remote disk for teams on macOS that share big files, video first: it shows up in the Finder as **Floppy**, each disk a folder, and the files live on S3 object storage through JuiceFS. On macOS JuiceFS runs in a Lima VM (Debian) and reaches the Mac over SMB, because mounting it directly would need macFUSE's kernel extension. The parts: the engine in `macos/` (the `floppy` command and the VM), the menu bar app in `app/` that ships it (Homebrew cask, `packaging/`), the control plane in `server/` with its admin web in `web/`, the meta service in `meta/` (one Valkey per disk), the deploy for Dokploy in `deploy/`, the site in `site/`, shared tokens in `design/`, and the spikes in `spike/` (the patched JuiceFS is `spike/juicefs-replica`).

Public repository: code, comments, docs and commits in English; README.md is mirrored in README.it.md in the same commit.

## Glossary

The full glossary is in [docs/CONVENTIONS.md](docs/CONVENTIONS.md#glossary). The terms that matter most when changing code:

- **Disk / Floppy**: what the user sees in the Finder. **Volume**: the JuiceFS filesystem behind it, with a name (`VOLUME_NAME`) and its metadata.
- **Metadata**: per disk in its own Valkey on the meta service, with a replica in each VM that sees the disk; in the single-user zip, SQLite **inside the VM** (`/var/lib/floppy/meta.db`), so losing that VM makes its bucket unreadable.
- **Cache**: blocks already downloaded, on the VM's disk. **Warmup** is "Make available" (`floppy warmup`), **evict** is "Free up space" (`floppy free`).
- **The user**: someone editing with CapCut, not technical. **Simone**: who develops and decides.

## Commands

```bash
make check                               # the full quality pass; make lint: bash -n, shellcheck, prose language
make app                                 # dist/app/Floppy.app and its DMG, the engine inside; needs Docker running
scripts/release-app.sh                   # after a `chore(release): X.Y.Z` bump of app/package.json, tauri.conf.json, app/Cargo.toml: tag, GitHub release with notes from the commits (scripts/release-notes.sh), cask into essedev/homebrew-floppy (outward: only when Simone asks)
~/.floppy/bin/floppy status                # setup|start|stop|status|warmup <path>|free <path>|activity|trash|uninstall
~/.floppy/bin/floppy disks apply <secrets.json  # team disks from ~/.floppy/disks.json; `disks clear` on revocation
FLOPPY_HOME=/tmp/fd FLOPPY_VM=floppy-dev macos/install.sh   # a development VM beside the real one

# Apply repo changes to this Mac's install (keeps VM and volume)
cp macos/bin/floppy ~/.floppy/bin/ && cp macos/vm/guest/* ~/.floppy/vm/guest/ \
  && ~/.floppy/bin/floppy setup

# Inside the VM
export LIMA_HOME=~/.floppy/vms
~/.floppy/lima/bin/limactl shell --workdir / floppy <cmd>   # e.g. sudo journalctl -u floppy-mount

# Local spike (Garage, PostgreSQL, Valkey, two clients): commands in .claude/rules/spike.md

# Site (Astro + Starlight; the product name lives in site/src/brand.ts)
cd site && pnpm dev            # pnpm build, pnpm check

# Menu bar app (Tauri; reads `floppy status --json`, so a change there changes app/src-tauri/src/engine.rs)
cd app && FLOPPY_BIN=/path/to/fake-floppy pnpm tauri dev   # without FLOPPY_BIN: ~/.floppy/bin/floppy
cd app && pnpm dev             # views in a browser: ?view=panel|settings|disk|welcome&scenario=... (disk: &tab=activity|trash) (sign-in: &stage=signin&scenario=auth_<state>)
cd app/src-tauri && FLOPPY_E2E_SERVER=<url> FLOPPY_E2E_CONTAINER=<server container> cargo test e2e -- --ignored --test-threads=1   # sign-in and team disks against a server stack (setup in account/e2e.rs)
app/scripts/render-marks.sh    # tray PNGs and app icons from design/mark/

# A local instance a real install can use (http://127.0.0.1:4583); the team chain end to end with a dev VM
deploy/local/stack.sh up|down|mail <email>|hosts
deploy/local/e2e.py
STACK=manual deploy/local/stack.sh up   # a second instance, manual storage on a real S3 (:4593); e2e.py takes STACK too

# Control plane: server/ (commands in .claude/rules/server.md); admin web, dev on the fake API
cd web && pnpm dev             # http://localhost:4508; pnpm typecheck, lint, build
make meta-check                # the meta service (meta/): one Valkey per disk
FLOPPY_JUICEFS_DIST=spike/juicefs-replica/dist deploy/check/check.py   # the Dokploy compose behind Traefik on 443, 25 checks

# Claude through MCP (DECISIONS #29): the team's server at /mcp/ (server/, oauth/ and mcp/), this Mac's on stdio
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | app/target/debug/floppy-app mcp
```

## Gotchas

**Never `juicefs mount` directly on macOS.** FUSE-T fails, macFUSE's FSKit backend hangs, and the kernel extension sends every Mac through recovery mode. JuiceFS runs in the VM, the Mac mounts SMB.

**The mount point must stay `/Volumes/Floppy`, but it is never assumed.** CapCut references videos by full path, and macOS picks `Floppy-1` when a previous mount's folder is left (after sleep). Mount only on a free name (`mount_share`), read the mount point from `/sbin/mount` (`mount_point`), never `VOLUME=/Volumes/Floppy`.

**Never unmount or remount a mounted disk automatically.** A disk busy with a large copy looks dead; forcing it broke a `cp` halfway. `tick` mounts only a missing disk (`mount_share keep`); remounting is only in `floppy start`, asked for by the user, never with `force`.

**The VM does not see the Mac's disk.** JuiceFS's `--free-space-ratio` checks the VM's disk, and a full Mac broke the VM's filesystem. Space limits are computed on the Mac (`cache_limit_mb`, `RESERVE_MB`, `tick`), and a VM Lima calls "Running" can be broken inside (`vm_healthy`).

**No script messages in the log, and fd 3 closed for limactl.** The error trap inherits redirections like `lima start >>lima.log` and once filled a disk: messages go to fd 3, the terminal saved at startup. `lima start` leaves a host agent that would keep fd 3 open forever: `lima()` closes it (`3>&-`).

**macOS `/bin/bash` is 3.2**: with `set -u` an empty `"${a[@]}"` is an error. Write `${a[@]+"${a[@]}"}`.

**No external network during install.** An office DNS blocked `cloud.debian.org`: the Debian image, the patched JuiceFS and the `.deb` closure of `PACKAGES` (`guest-install.sh`) travel in the app bundle (`scripts/build-engine.sh`). The `.deb` files come from a `debian:trixie` container with an empty dpkg status, or `--download-only` misses what the container has.

**Samba's directory leases stay off** (`smb3 directory leases = no`): with them the Mac keeps every listed folder open, and a team disk can never be unmounted to apply lowered access.

**Team disks change mount options only in `disks apply`, with a plain unmount**: a busy disk is reported `pending` and retried, never forced.

**Lima's socket paths must fit in 104 characters**: a test `FLOPPY_HOME` needs a short real path, since Lima resolves symlinks.

**`systemctl is-active` with several units says yes if any is active.** One unit per call.

**Absolute paths for system tools**: `/usr/sbin` is not in every PATH (launchd, some shells). Write `/usr/sbin/diskutil`, `/usr/bin/osascript`, `/sbin/mount`.

**Do not uninstall or delete the VM to "start clean".** `floppy uninstall` and `limactl delete` throw away the metadata: the bucket's data becomes unreadable, and a reinstall with the same `VOLUME_NAME` collides with it. To apply changes, rerun `setup`.

**`setup` does not change the RAM of an existing VM**: `memory` in `lima.yaml` applies at creation; otherwise `limactl edit --memory <GiB> floppy` while stopped. The rest (`vm/guest/`, config) `setup` reapplies.

**JuiceFS behind Samba needs `--enable-xattr`**, and Samba's `fruit` module the `samba-vfs-modules` package: without them deleting from the Mac gives `Operation not supported`.

**JuiceFS does not cache uploaded blocks** unless `--cache-large-write` is on (off by default).

**`nsmb.conf` gets a `# floppy begin/end` block, never a rewrite**: the file holds the user's configuration. The block raises `max_resp_timeout` for the VM's name and address (macOS gives up on an SMB read after 30 s), maps the name with `addr=` (a fallback: a name that resolves is mounted by address, `share_server`) and cuts `dir_cache_min`/`dir_cache_max` to 1 s, or colleagues' new files arrive 30 s late.

**Never `docker network prune`**: the development Mac's networks belong to other projects too; every compose file here pins its subnet.

**Samba multiplies metadata operations** (about 2,700 for a listing of 300 files). Do not tune Samba or caches around it: reads must come from the local replica (DECISIONS #5).

**Names in the VM are not the Mac's.** Samba (`catia`) stores the characters Windows forbids as U+F001-F027: the Finder's `A / B` is `A : B` on the Mac and `A` U+F022 `B` in the VM, the form of the activity and the trash. Translate when crossing: `vm_path` (Mac to VM), `mac_path` in `core.rs`, `finderName` in the app.

**Unmount SMB with `-f` in scripts**: a gentle unmount can leave a stale session, and the next mount answers I/O errors.

## Secrets

`spike/.env` (with the PostgreSQL and Valkey passwords), `my-test.env`, `~/.floppy/config.env` and every `dist/*.zip` hold S3 keys and the SMB password: they are outside git (`*.env`, `dist/`) and are never printed, not even in a debug log. To check a file, show the key names, not the values. `package.sh` builds the zip with `umask 077`. OVH S3 keys open every bucket in the project: a tester needs a dedicated user.

## Touch every surface

A change to how the disk behaves almost always goes through several files:

- `macos/bin/floppy` (Mac side) and `macos/vm/guest/` (VM side: `floppy-mount`, `guest-install.sh`, `smb.conf`, the systemd service).
- `macos/vm/lima.yaml` if the VM changes, and then the `setup` gotcha above.
- `macos/config.env.example` if `config.env` gets a new option.
- `macos/install.sh` and `scripts/package.sh` if what goes into the zip changes.
- `spike/juicefs-replica/` (Dockerfile tag, patches) and `JUICEFS_VERSION` in `guest-install.sh` together if JuiceFS changes: `package.sh` refuses a mismatch.
- `docs/ARCHITECTURE.md`: the "Configuration" and "macOS package" sections, and "Measured performance" if a number changes.
- `README.md` and `README.it.md` together.

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
- [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md): the control plane's tables, secrets at rest, state machines.
- [docs/DECISIONS.md](docs/DECISIONS.md): what was decided and what it ruled out, cited as `DECISIONS #N`.
- [docs/ROADMAP.md](docs/ROADMAP.md): next steps.
- [docs/CYCLES.md](docs/CYCLES.md): what was done and when.
- [docs/architecture.html](docs/architecture.html): the team architecture (control plane, central Valkey with a replica per VM, login) as diagrams and flows; a proposal until it lands in ARCHITECTURE.md.
- [.claude/rules/](.claude/rules/): rules per path (app, server, site, spike), loaded when a file under it is touched.
- [docs/concepts/](docs/concepts/): HTML concepts with variants side by side, one file per design decision.
