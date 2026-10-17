# Decisions

Decisions that bind the future, each with the alternative it ruled out. Cite them as `DECISIONS #N`. Status: proposed, active, superseded. The story of how they came about is in [CYCLES.md](CYCLES.md), the reasoning in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. JuiceFS in a Linux VM, shared to the Mac over SMB

**Active, 06/10/2026.** JuiceFS needs FUSE; on macOS only macFUSE's kernel extension works, and it forces every Mac through recovery mode. JuiceFS runs in a Lima VM and the Mac mounts it over SMB on the private Mac-VM link, with no kernel extension.
**Ruled out:** FUSE-T and macFUSE's FSKit backend (do not work with JuiceFS), the kernel extension, Docker Desktop as the Linux box (too heavy). A native FSKit file system stays the long-term goal (see #9).

## 2. One codebase, self-hosted or hosted, control plane off the data path

**Active, 16/10/2026.** The same code runs as a self-hosted instance with one organization or as a hosted service with many. The control plane hands out access and configuration only; files flow between the Mac, the metadata server and S3, so mounted disks keep working when it is down.
A Valkey replica copies its whole instance, so the hosted service gives each organization its own Valkey instance; otherwise every Mac would receive every customer's metadata.
**Ruled out:** a single central server as the only way to run it, a control plane that proxies data.

## 3. Licenses: MIT for the app and the command, AGPL-3.0 for the control plane

**Active, 16/10/2026.** Protection matters only where hosting happens; the client side stays free to reuse. The author keeps the copyright, so a hosted service and commercial licenses stay possible; outside contributions to the control plane need a CLA first.
**Ruled out:** MIT everywhere (anyone could resell a hosted copy), AGPL everywhere.

## 4. Only standard S3 and standard database protocols in the core

**Active, 17/10/2026.** The core uses plain S3 and a Redis-compatible metadata server; no provider API. Per-person S3 keys were dropped: the S3 key lives in the volume's metadata, a revoked Mac loses it with its metadata access, and the key is rotated by pasting a new one (`juicefs config`).
**Ruled out:** provisioning S3 users through the OVH API, which would tie the product to one provider.

## 5. Shared metadata on Valkey with a local replica in every VM

**Active, 17/10/2026.** A central Valkey holds the metadata; each VM runs a replica of it, and a patched JuiceFS reads from the replica and writes to the master, with read-your-writes. Measured at 15 ms: a cold listing of 300 files from the Finder takes 0.2-0.3 s instead of 75-117 s, because Samba's ~2,700 metadata operations for that listing become local. Valkey because Redis moved to source-available licenses in 2024.
**Ruled out:** PostgreSQL reached over the WAN (117 s), tuning Samba and caches around the round trips (vetoing `._` files, Finder attributes in a local index: they break sharing), plain objects with rclone (no locks, renames copy data), writing a file system with replicated metadata from scratch.

## 6. Writes as optimistic transactions, offline as read-only

**Active, 17/10/2026.** A write runs JuiceFS's own Go logic on replica reads, then one script on the master checks every read and applies the writes; on a conflict it retries with WATCH. Every hot write costs one round trip instead of 3-6. When the master is unreachable, reads keep coming from the replica and writes fail at once with "read-only file system"; a slow master must not count as unreachable.
**Ruled out:** rewriting each operation in Lua (two copies of the logic), asynchronous metadata writes (a file shown as saved before the master knows it).

## 7. One database user per Mac

**Active, 16/10/2026.** Each Mac gets its own Valkey user, used for its writes and for its replica's replication. Revoking it stops both, and a lost laptop is revoked alone.
**Ruled out:** one user per person, a shared user.

## 8. Small-file creation target: 100 files in under 20 seconds at 15 ms

**Active, 17/10/2026.** Each file created from the Finder costs about 9 sequential metadata writes over SMB, each already one round trip. The earlier 10-second target was set before this was known.
**Ruled out:** asynchronous metadata to go lower (see #6).

## 9. Menu bar app in Tauri, macOS only

**Active, 17/10/2026.** Tauri from the Portsage skeleton, React in the webview, the `fluix` command as the engine; one design system shared with the control plane's web. Finder badges, if ever needed, come as a small Swift extension next to the app.
**Ruled out:** a SwiftUI app (two design systems), File Provider (downloads whole files, breaks scrubbing).

## 10. Distribution through a Homebrew cask

**Active, 17/10/2026.** The app ships as a cask in its own tap, like Portsage. Homebrew needs administrator rights once to install itself; installing and using the disk does not.
**Ruled out:** a DMG in `~/Applications` (kept as the fallback if a team has standard accounts), a signed and notarized DMG for now.

## 11. Identity: the drive, Grafite faceplate with the Essenziale icons

**Active, 17/10/2026.** The mark is a drive's slot and LED on one axis; the faceplate is a dark device in both themes; the LED is the state (green connected, orange blinking activity, red unreachable, off). Details come from the object: an LED in its bezel, Departure Mono readouts, the slot as list marker. Concepts: [concept-b-identity](concepts/concept-b-identity.html), [concept-c-app](concepts/concept-c-app.html).
**Ruled out:** Portsage's look, the Diskette and Studio directions, generic UI-kit details (pills, glowing dots, colored side stripes).

## 12. App and onboarding behavior

**Active, 17/10/2026.** The panel puts the state first with one large action; "Turn off" instead of "Eject"; a full cache frees the least recently used blocks with a warning; automatic reconnection every 30 s; Settings in their own window. First run in a dedicated window; sign-in in the browser with a magic link and a 6-digit code as fallback. The admin web uses a left sidebar; removing a person leaves a persistent "change the key" reminder; members see only their own Macs. Concepts: [concept-d-onboarding](concepts/concept-d-onboarding.html), [concept-e-admin](concepts/concept-e-admin.html).
**Ruled out:** the projects-first and Finder-like panels, Eject, blocking a project when the cache is full, sign-in inside the menu bar panel.

## 13. Website with Astro and Starlight

**Active, 17/10/2026.** The public site is static content: presentation, docs for installers and self-hosters, changelog, download; Starlight gives docs, search and English and Italian. The application part is the control plane's web.
**Ruled out:** Next.js (an application framework for a content site).

## 14. The product is called Floppy

**Active, 17/10/2026.** The product is renamed from "Fluix" to "Floppy": the disk, `/Volumes/Floppy`, the `floppy` command, the VM, the services and the data folder. "Fluix" collided with Fluix Limited (fluix.io, document workflow software, US filing 97697627 in class 9), and the name ends up in `/Volumes/<Name>`, which CapCut projects reference, so it had to change before the pilot. Checked for "Floppy": no active product with that name in storage, sync or video; the only relevant US mark, FLOPPY by Hyperfoo, Inc. (Reg. 5310681, registered 17/10/2017, data software), is dead, cancelled under Section 8. EUIPO and UIBM are still to check.
Criteria that emerged while choosing: a real word of one or two syllables, read the same in Italian and English; taken from the object the identity draws, a drive (DECISIONS #11); no suffixes like -ly, -bay, -box, -ix, -ite; a conflict counts only in software, storage or video. Domains are not a requirement and are set aside for now: floppy.com is held by Arrow, floppy.app and getfloppy.com are for sale.
**Ruled out:** keeping "Fluix" for a commercial product; coined names with a suffix; requiring a free domain to pick the name.
