---
paths:
  - "spike/**"
---
# The spike (spike/)

The local test bench for the data path and the team engine: Garage as S3, PostgreSQL, Toxiproxy, a central Valkey with a replica per client, and two clients (`drive`, `drive-b`) that the Mac mounts over SMB; the `perdisk` profile adds three disks, each with its own Valkey, on one client (`drive-p`). Secrets in `spike/.env`, outside git.

```bash
spike/setup.sh                 # Garage, PostgreSQL, Toxiproxy, drive; secrets in spike/.env
spike/bench.sh                 # upload, cold/warm reads, seeks, warmup
LINE_MBPS=100 docker compose -f spike/docker-compose.yml --profile shaped up -d shaper   # one real link queue
DOWNLOAD_LIMIT_MBPS=0 spike/data-bench.sh  # cold reads inside the box and from the Mac, seeks, scrubbing
spike/meta-bench.sh            # metadata over SMB: SQLite vs PostgreSQL
spike/ovh-bench.sh [line upload cold meta edit]   # the same measures on the real install's first disk, plus an editing session; in a _test folder it removes
spike/juicefs-replica/build.sh # patched juicefs into dist/ (skipped while dist/BUILD matches)
spike/juicefs-replica/test.sh  # the patches' Go tests
JUICEFS_SOURCE=replica docker compose -f spike/docker-compose.yml --profile replica build drive drive-b
spike/acceptance.py [--baseline] [t01 ...]   # two clients, central Valkey + replicas, 17 checks
SMB_FORCE_USER=drive spike/perdisk.py [t14 ...]   # per-disk permissions: one Valkey and one bucket per disk, 52 checks
spike/metatls.py               # disks' Valkey behind Traefik, one TLS port routed by name (profile metatls), 14 checks
```

## What the spike leaves on the Mac

Everything is in `spike/.local/`, outside git: `mnt/<client>` are the Mac's mount points (`drive`, `drive-b`, `drive-p`, `drive-t`; `MNT` overrides the shell benches'), created before mounting and removed after unmounting; `state-a` and `state-b` are the `/state` of `drive` and `drive-b` (their SQLite metadata). The share is `FloppySpike`, never `Floppy`: macOS names an SMB volume after its share, and a spike mount would look like the real disk in the Finder. `grep -r` over the repo during a test enters the mounted share; `rg` and VS Code's search skip it (`.gitignore`).

## Gotchas

**Simulate bandwidth with the shaped link, not Toxiproxy nor JuiceFS's limits.** Toxiproxy's `bandwidth` is per connection and JuiceFS opens many: 2.7 GB "went through" at 150 MB/s with a 37 MB/s limit. JuiceFS's `--download-limit` lets the first megabyte through at once and then stalls, which hid how readahead competes with a new read. Toxiproxy is for latency; bandwidth is the `shaped` profile (`LINE_MBPS`, with `DOWNLOAD_LIMIT_MBPS=0`), one queue for everything Toxiproxy sends.

**Measure reads from the Mac through its cache too.** With `F_NOCACHE` the macOS SMB client sends one read at a time; through its cache it sends 128 KB pieces out of order, up to 40 MB ahead, and a test that stops reading halfway counts those as waste. Compare whole-file reads, and count GETs in Garage's log, not only JuiceFS's metrics: cancelled downloads are missing there.

**The spike needs the SMB timeout too.** The real install raises `max_resp_timeout` for the VM's address; the spike talks to `localhost` and `127.0.0.1`, so the same block (`# floppy-spike begin/end` in `~/Library/Preferences/nsmb.conf`) has to be there, or cold reads fail after 30 s.

**macOS mounts a server and share only once.** A second mount of `localhost/FloppySpike`, even on another port, fails with "File exists": the spike reaches the second client by address (`127.0.0.1`).

**Unmount from the Mac before stopping or recreating a client.** A server that goes away under a mount brings up macOS's "Server connections interrupted" dialog on Simone's screen, and the mount disappears by itself 25 to 70 s later. Seven such runs (stop, recreate, force unmount right after the stop, a file held open, a Finder window open, the whole stack stopped) left no ghost, but the ghosts found in the Finder's Computer view (a disk with no URL and no mount behind it) were all spike mount points of past runs. A ghost does not go with the Finder's `eject`, `diskutil unmount force`, or a mount and unmount at the same path: only `killall Finder` clears it. The harnesses unmount first (`unmount()` in `acceptance.py`, `mac_unmount()` in `perdisk.py`, the shell benches, `setup.sh` before rebuilding `drive`), and the Python ones and `meta-*.sh` raise an error instead of going on while the share is still mounted.
