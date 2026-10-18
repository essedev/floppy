# Conventions

The engine is bash and configuration (Lima, systemd, Samba), and the sections below are mostly its rules. The control plane and the meta service are Python (`server/`, `meta/`, rules in `.claude/rules/server.md`); the menu bar app is Tauri with TypeScript and Rust in the layout of [Portsage](https://github.com/essedev/portsage) (rules in `.claude/rules/app.md`); the admin web and the site are TypeScript.

## Language

Public repository: code, comments, docs and commits are in English.

| Layer | Language |
|---|---|
| Identifiers, file names, comments, commit messages | English |
| `README.md` | English, canonical |
| `README.it.md` | Italian, a translation of `README.md`, updated in the same commit |
| `docs/*`, `CLAUDE.md` | English |
| `docs/concepts/*.html` | Italian, the language of the UI they show (declared in `.prose-allow`) |
| Git history before the rename | stays as it is |

`make lint` runs `scripts/check-prose.py`, which fails on Italian prose outside the allowed paths and on a README pair out of step.

## Glossary

| Term | Meaning |
|---|---|
| Disk, Floppy | What the user sees in the Finder, mounted at `/Volumes/Floppy`. A team's disks are its folders, `/Volumes/Floppy/<disk>` (DECISIONS #20). |
| Volume | The JuiceFS filesystem behind a disk, with a slug name (`VOLUME_NAME`) never shown to the user. One per Mac in the test package; in the team version one per disk, its metadata in the disk's own Valkey instance (DECISIONS #23). |
| Metadata | JuiceFS's tree of files and folders. In the test it lives in SQLite inside the VM; in the team version in each disk's Valkey instance, with a replica in the VM of every device that may see the disk. |
| Device | A Mac registered to the control plane, with its own token, Valkey password and, in managed mode, S3 key. The web and the server say device, not Mac. |
| Meta service | `meta/`: the container that runs one Valkey instance per disk, reached through the reverse proxy on one TLS port (DECISIONS #25). |
| Cache | Blocks already downloaded, on the VM's disk. |
| Warmup, "Make available" | Preloading a folder into the cache (`floppy warmup`). "Rendi disponibile" in the Italian UI. |
| Evict, "Free up space" | Dropping a folder from the cache (`floppy free`). "Libera spazio" in the Italian UI. |
| Cold, warm | Reads of blocks not in the cache (over the network) or already in it. |
| Spike | The Docker stack in `spike/`, for measuring without a real network. Not the product. |
| Tester | Someone running a test zip on their own Mac. |
| Pilot | One shared disk used for real work for one or two weeks (see [ROADMAP.md](ROADMAP.md)). |

## Bash scripts

- **`set -euo pipefail`** at the top of every script. A command that can legitimately fail is written explicitly (`|| true` with a comment on why), never left to swallow the error silently.
- **Errors with `die`**: a message that says what to do, on stderr, and a non-zero exit. No debug `echo` left in the code.
- **Idempotent**: `setup`, `guest-install.sh` and `spike/setup.sh` can be rerun safely. A step that creates something first checks whether it is already there.
- **Absolute paths for macOS system tools** (`/usr/sbin/diskutil`, `/usr/bin/osascript`, `/sbin/mount`): under launchd the PATH is reduced.
- **No assumed state**: mount point, VM address and VM status are read from the system at every use, not saved in global variables.
- **Functions first, dispatch at the bottom**: `cmd_<name>` for every subcommand, a final `case` that calls them.
- **Checks**: `make check` runs `bash -n` and `shellcheck` on every script, and the prose check.

## Configuration

- **Every new option** in `config.env` goes into `macos/config.env.example` with a comment, and has a sensible default in the script that reads it (`${NAME:-default}`).
- **Measured values** (VM memory, buffer, prefetch) carry the number that justifies them in the comment next to them and point to `docs/ARCHITECTURE.md`.

## Secrets

Never a secret in a tracked file, in a command-line argument visible in `ps`, or in output. They go through files with `600` permissions or through stdin (e.g. `curl -K -`).

Known exception, to close: the SMB password between Mac and VM goes through the `mount volume` URL (`macos/bin/floppy`, `mount_share`) and in the spike through the `mount_smbfs` one, visible in `ps` for a moment. It holds only on the private Mac-VM link; the way out is the macOS Keychain.
