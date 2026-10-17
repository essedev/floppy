# Roadmap

The principle is the one in [ARCHITECTURE.md](ARCHITECTURE.md#guiding-principle): build only what the previous step proved necessary. Work already done is in [CYCLES.md](CYCLES.md), the decisions in [DECISIONS.md](DECISIONS.md).

## 1. Data path on OVH (next)

The data-path spike is closed (CYCLES 8, DECISIONS #15): on a simulated 100 Mbps link cold reads over SMB reach 86-93% of the line, a cold seek 0.38 s, the first read after a jump 0.40 s. Left: the same measures on OVH Milan with a zip that formats 1 MB blocks (the same run as the tester's new zip, ROADMAP 2), and, during a large copy, whether the file's mtime changes at every stat, which JuiceMount found stalls the macOS client for about 73 s on the file's lock.

## 2. Name

Floppy is chosen and the repository is renamed (DECISIONS #14). Left: the Italian marks from the 1990s on UIBM (FLOPPYNEWS in classes 9 and 42 first; no live FLOPPY mark for software in the EU), rename the GitHub repository and the URL that points to it (`site/src/brand.ts`, `docs/concepts/concept-d-onboarding.html`), and hand the tester a new zip before anyone uses the disk with CapCut projects. Domains are set aside for now.

## 3. Build

- **Design on paper**: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) is done and reviewed (DECISIONS #16-#22), and ARCHITECTURE.md has the control plane, access, storage modes and resilience parts. Left: the rest of the team architecture from [architecture.html](architecture.html) into ARCHITECTURE.md (components, flows, app states, failure modes, the three ways to run it).
- **Engine**: the patched JuiceFS (both patches: metadata replica, whole-block reads) and a Valkey replica in the VM's offline package set, `floppy status --json`, the metadata URL from the app instead of a config file, the SMB password in the Keychain.
- **Fast failure when a file cannot be read**: with the network down, a read of blocks not in cache waits for JuiceFS's retries and for the Mac's SMB timeout, which Floppy raised to 600 s. It should fail at once, the way JuiceMount refuses reads of files not kept offline in 4-67 ms from a local index. It needs to know, per path, what is in the cache; `floppy warmup` loads it but does not record it.
- **The Mac stays awake while data moves**: `caffeinate -i` during a copy, an upload or a warmup, released a couple of minutes after the traffic stops (as JuiceMount does), so sleep does not cut a transfer halfway. No administrator rights needed.
- **Keeping the JuiceFS patches alive**: JuiceFS stays pinned (v1.4.1) and moves only when a release brings something we need (a security or bug fix, performance), not with every Floppy release. The patches (0001 metadata replica, 0002 whole-block reads) move from the files in `spike/juicefs-replica/` to a branch of a fork (`essedev/juicefs`), one commit per change, so an update is a rebase. A scheduled GitHub Actions job applies them to each new JuiceFS release and runs the Go tests, so a break shows up before we need the update. An update ships only after the Go tests, JuiceFS's own Redis metadata tests and the acceptance suite pass, and after a run with mixed versions (some Macs on the old JuiceFS, some on the new, one Valkey); if mixed versions do not hold, the control plane enforces a minimum version. Checked on 17/10/2026: no JuiceFS release after v1.4.1 yet; against `main` (212 commits later) 25 of the patch's 27 hunks in `redis.go` apply, and `doCleanStaleSession` and `doRemoveXattr` conflict.
- **Control plane**: phases 1 and 2 are done (`server/`, CYCLES 9). Left: the OVH, AWS and MinIO adapters, a shared store for rate limits, cleanup of provider keys orphaned by a failed commit, and the per-disk permissions above.
- **Per-disk permissions** (next): none, read or read-write per person and disk, with one Valkey instance and one bucket per disk; a spike verifies it, then DECISIONS #2, #19, #20, #22, the schema and the control plane change.
- **App**: a first version exists (`app/`: menu bar, panel, Settings, first run, against a fake engine). Left: sign-in and enrolment against the control plane, the disks as folders, the engine lock so the app can run `tick`.
- **Distribution and first instance**: the Homebrew cask, then the first self-hosted instance on Dokploy with a central Valkey and S3 in Milan.

## 4. Pilot

One shared disk, one or two weeks of real work in CapCut, on the first instance. To measure: scrubbing on uncached clips, uploading footage, first opening of a folder with many videos, how the disk behaves when the line is busy or drops. Every problem noted with what and when: it decides what the app does next.

## 5. Alongside

- **The JuiceFS patch upstream**: propose the read replica and the optimistic transactions to JuiceFS, so the fork does not have to live forever.
- **Site**: Astro and Starlight in `site/` (DECISIONS #13), with the domain once there is one.
- **Comparison with SpaceFS**: their 7-day trial on the same project as the pilot.
- **At the end of the tests**: revoke the test S3 key and empty the test bucket.
