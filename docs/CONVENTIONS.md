# Conventions

The code today is bash and configuration (Lima, systemd, Samba). The Tauri app, when it arrives, follows the TypeScript and Rust layout of [Portsage](https://github.com/essedev/portsage).

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
| Disk, Floppy | What the user sees in the Finder, mounted at `/Volumes/Floppy`. |
| Volume | The JuiceFS filesystem behind the disk, with a name (`VOLUME_NAME`) and its metadata. One per Mac in the test, one shared with PostgreSQL later. |
| Metadata | JuiceFS's tree of files and folders. In the test it lives in SQLite inside the VM. |
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
