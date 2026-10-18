# Roadmap

The principle is the one in [ARCHITECTURE.md](ARCHITECTURE.md#guiding-principle): build only what the previous step proved necessary. Work already done is in [CYCLES.md](CYCLES.md), the decisions in [DECISIONS.md](DECISIONS.md).

## 1. The system online (next)

The first instance runs on Dokploy (CYCLES 11): the control plane, the admin web and the meta service behind Dokploy's shared Traefik, one certificate per disk name, mail through SMTP, and the team's first disk in manual mode on OVH Milan with bucket versioning (old versions kept 7 days). Simone's Mac mounts it from the `v0.1.1` cask. Left, in order:

1. **Backups before real files**: PostgreSQL through Dokploy's scheduled backup to S3; the disk's metadata through a daily `juicefs dump` from the control plane, kept outside the bucket too, with a restore tried once. JuiceFS's own hourly metadata copy in the bucket stays, untested. A copy of the bucket on a second provider can wait while the team works on copies.
2. **The team on it**: invitations from the admin web with access to the disk; each person installs with `brew install --cask essedev/floppy/floppy`, sets the instance's address and signs in.
3. **The measures on OVH** on that instance (ROADMAP 2).
4. **Small things seen on the way**: a change of access reaches the app at its next heartbeat, up to 60 s later; the Finder's sidebar may keep showing nothing after the share is remounted by name until the Finder is relaunched; a failed mount leaves a Finder dialog that blocks the engine until someone clicks OK; the SMB password between Mac and VM is in `osascript`'s arguments (to the Keychain).

## 2. Data path on OVH

The data-path spike is closed (CYCLES 8, DECISIONS #15): on a simulated 100 Mbps link cold reads over SMB reach 86-93% of the line, a cold seek 0.38 s, the first read after a jump 0.40 s. First numbers on OVH Milan from the manual-mode run: 55 MB/s written with fsync, 57 MB/s read cold from the Mac. Left: the same measures as the spike on the first instance, and, during a large copy, whether the file's mtime changes at every stat, which JuiceMount found stalls the macOS client for about 73 s on the file's lock.

## 3. Build

The team version works end to end on a local stack (CYCLES 10): control plane, meta service, admin web, app and engine, with per-disk access.

- **Design on paper**: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) is built (migrations 0001-0003), and ARCHITECTURE.md has the control plane, access, storage modes, deploy, team disks and resilience. Left: the rest of the team architecture from [architecture.html](architecture.html) into ARCHITECTURE.md (flows, app states, failure modes, the three ways to run it), and architecture.html itself still draws the key scopes of DECISIONS #22 instead of #24.
- **Fast failure when a file cannot be read**: with the network down, a read of blocks not in cache waits for JuiceFS's retries and for the Mac's SMB timeout, which Floppy raised to 600 s. It should fail at once, the way JuiceMount refuses reads of files not kept offline in 4-67 ms from a local index. It needs to know, per path, what is in the cache; `floppy warmup` loads it but does not record it.
- **The Mac stays awake while data moves**: `caffeinate -i` during a copy, an upload or a warmup, released a couple of minutes after the traffic stops (as JuiceMount does), so sleep does not cut a transfer halfway. No administrator rights needed.
- **Keeping the JuiceFS patches alive**: JuiceFS stays pinned (v1.4.1) and moves only when a release brings something we need (a security or bug fix, performance), not with every Floppy release. The patches (0001 metadata replica, 0002 whole-block reads, 0003 keys from the environment) move from the files in `spike/juicefs-replica/` to a branch of a fork (`essedev/juicefs`), one commit per change, so an update is a rebase. A scheduled GitHub Actions job applies them to each new JuiceFS release and runs the Go tests, so a break shows up before we need the update. An update ships only after the Go tests, JuiceFS's own Redis metadata tests and the acceptance suite pass, and after a run with mixed versions (some Macs on the old JuiceFS, some on the new, one Valkey); if mixed versions do not hold, the control plane enforces a minimum version. Checked on 17/10/2026: no JuiceFS release after v1.4.1 yet; against `main` (212 commits later) 25 of the patch's 27 hunks in `redis.go` apply, and `doCleanStaleSession` and `doRemoveXattr` conflict.
- **Control plane**: phases 1 and 2 and per-disk access are done (`server/`, CYCLES 9 and 10). Left: the OVH, AWS and MinIO adapters (Garage is built), a shared store for rate limits (with the hosted service), and cleanup of provider keys orphaned by a failed commit.
- **App**: make a change of access immediate (ROADMAP 1). Drop "Later" in the first run and retire the zip and its docs once the team uses the cask.

## 4. Pilot

One shared disk, one or two weeks of real work in CapCut, on the first instance. To measure: scrubbing on uncached clips, uploading footage, first opening of a folder with many videos, how the disk behaves when the line is busy or drops. Every problem noted with what and when: it decides what the app does next.

## 5. Alongside

- **Name**: Floppy is chosen and the repository renamed (DECISIONS #14); the old `essedev/fluix` address redirects. Left: the Italian marks from the 1990s on UIBM (FLOPPYNEWS in classes 9 and 42 first; no live FLOPPY mark for software in the EU). Domains are set aside for now.
- **The JuiceFS patch upstream**: propose the read replica and the optimistic transactions to JuiceFS, so the fork does not have to live forever.
- **Site**: Astro and Starlight in `site/` (DECISIONS #13), with the domain once there is one. The header links to sign in on the hosted web (`brand.dashboard`, `app.` on the domain); the Mac app is installed through the Homebrew cask on the landing (DECISIONS #10).
- **Comparison with SpaceFS**: their 7-day trial on the same project as the pilot.
- **At the end of the tests**: revoke the test S3 key and empty the test bucket.
