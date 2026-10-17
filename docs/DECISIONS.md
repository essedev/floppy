# Decisions

Decisions that bind the future, each with the alternative it ruled out. Cite them as `DECISIONS #N`. Status: proposed, active, superseded. The story of how they came about is in [CYCLES.md](CYCLES.md), the reasoning in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. JuiceFS in a Linux VM, shared to the Mac over SMB

**Active, 06/10/2026.** JuiceFS needs FUSE; on macOS only macFUSE's kernel extension works, and it forces every Mac through recovery mode. JuiceFS runs in a Lima VM and the Mac mounts it over SMB on the private Mac-VM link, with no kernel extension.
**Ruled out:** FUSE-T and macFUSE's FSKit backend (do not work with JuiceFS), the kernel extension, Docker Desktop as the Linux box (too heavy). A native FSKit file system stays the long-term goal (see #9).

## 2. One codebase, self-hosted or hosted, control plane off the data path

**Active, 16/10/2026.** The same code runs as a self-hosted instance with one organization or as a hosted service with many. The control plane hands out access and configuration only; files flow between the Mac, the metadata server and S3, so mounted disks keep working when it is down.
A Valkey replica copies its whole instance, so every disk has its own Valkey instance (#23), self-hosted and hosted alike; no Mac ever receives another team's or another disk's metadata.
**Ruled out:** a single central server as the only way to run it, a control plane that proxies data.

## 3. Licenses: MIT for the app and the command, AGPL-3.0 for the control plane

**Active, 16/10/2026.** Protection matters only where hosting happens; the client side stays free to reuse. The author keeps the copyright, so a hosted service and commercial licenses stay possible; outside contributions to the control plane need a CLA first.
**Ruled out:** MIT everywhere (anyone could resell a hosted copy), AGPL everywhere.

## 4. Only standard S3 and standard database protocols in the core

**Superseded by #22, 17/10/2026.** The core uses plain S3 and a Redis-compatible metadata server; no provider API. Per-person S3 keys were dropped: the S3 key lives in the volume's metadata, a revoked Mac loses it with its metadata access, and the key is rotated by pasting a new one (`juicefs config`).
**Ruled out:** provisioning S3 users through the OVH API, which would tie the product to one provider.

## 5. Shared metadata on Valkey with a local replica in every VM

**Active, 17/10/2026.** A central Valkey holds the metadata; each VM runs a replica of it, and a patched JuiceFS reads from the replica and writes to the master, with read-your-writes. Measured at 15 ms: a cold listing of 300 files from the Finder takes 0.2-0.3 s instead of 75-117 s, because Samba's ~2,700 metadata operations for that listing become local. Valkey because Redis moved to source-available licenses in 2024.
**Ruled out:** PostgreSQL reached over the WAN (117 s), tuning Samba and caches around the round trips (vetoing `._` files, Finder attributes in a local index: they break sharing), plain objects with rclone (no locks, renames copy data), writing a file system with replicated metadata from scratch, JuiceFS 1.4's Redis client-side cache (`client-cache=true`, checked 17/10/2026: it caches only attributes and lookups of existing names, not listings, extended attributes or the missing `._` names, leaves writes at 3-6 round trips, and can serve stale data, upstream #7674 and #7676; the patch refuses to run with it).

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

**Active, 17/10/2026.** Tauri from the Portsage skeleton, React in the webview, the `floppy` command as the engine; one design system shared with the control plane's web. Finder badges, if ever needed, come as a small Swift extension next to the app.
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

## 15. Data path: 1 MB blocks, 4 MB readahead, whole-block reads

**Active, 17/10/2026.** Volumes are formatted with 1 MB blocks and mounted with a 4 MB readahead, and the patched JuiceFS reads whole blocks inside a sequential stream (`spike/juicefs-replica/0002-*.patch`). Measured on a simulated 100 Mbps link: a cold seek in 0.38 s (0.68 s with 4 MB blocks), the first read after a jump in 0.40 s (0.78 s with the default readahead), cold reads over SMB at 86-93% of the line. The readahead is fixed rather than sized on the line: a short one wins where the line is slow, and on a fast line it still reads 13 times faster than a 60 Mbps clip needs. Numbers in [ARCHITECTURE.md](ARCHITECTURE.md#measured-performance).
**Ruled out:** 4 MB blocks; a readahead sized on the measured line; SMB multichannel (the gap it was meant to close came from the simulation and the block size); simulating the link with JuiceFS's `--download-limit`, which lets bursts through and then stalls.

## 16. Control plane data model: UUIDv7, nothing deleted, keys scoped by organization

**Active, 17/10/2026.** Ids are UUIDv7 (`uuidv7()`, PostgreSQL 18): not enumerable across tenants, ordered by creation, and an organization moves between self-hosted and hosted with the same ids. No row is ever deleted, and the application role has no `DELETE` grant: entities (organizations, users, memberships, Macs, disks) carry `deleted_at` with partial unique indexes, records (invitations, sign-ins, sessions, keys, rotations, audit) end in a terminal state. Every reference between tenant tables is a composite `(organization_id, id)` foreign key. The one deletion is the hosted audit log's 13-month purge. Details in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).
**Ruled out:** `bigint` and UUIDv4 ids, hard deletes, `deleted_at` on records that are never hidden or restored, row-level security in the first migration (it comes before the hosted service opens).

## 17. Secrets at rest: encrypted when used, hashed when only checked

**Active, 17/10/2026.** What the control plane must use (the S3 secret, a hosted Valkey admin password, the SMTP password) is encrypted with AES-256-GCM under `FLOPPY_SECRET_KEY`, from the environment, bound to its row and column; a retired S3 secret is wiped. What it only checks (device, session, link and invitation tokens, each Mac's Valkey password) is a SHA-256; the 6-digit code is an HMAC with a key derived from the same secret, since a million values fall to any unkeyed hash. Losing the key loses no files: a team S3 secret is still in every volume's format, and with per-Mac keys (#22) the adapter creates a new control-plane key. A KMS comes before the hosted service takes paying customers.
**Ruled out:** keeping the S3 secret only in the volume format (the control plane needs it to format disks and probe the bucket), a slow or unkeyed hash for the code, a KMS from day one.

## 18. The key window closes only for exposures before the rotation

**Active, 17/10/2026.** Revoking a Mac that had a Valkey user, or removing a person who had one, opens the "rotate the S3 key" window; signing out from the app does not, since its owner keeps access. Closing a rotation (the admin confirms the old key is deleted at the provider) closes only the exposures that happened before the rotation started: a Mac revoked while it was applying may already have read the new key from its replica. The rotation waits for every disk, not for every Mac.
**Ruled out:** closing every reason at once, closing when the new key is applied, waiting for Macs that may be switched off.

## 19. Valkey users per Mac and per organization, not per disk

**Superseded by #23, 17/10/2026.** A consequence of #2 and #7: a Mac's replica copies the whole Valkey instance, every disk and its S3 key included, so one user per Mac on the organization's instance gives exactly what the Mac can read anyway. Valkey does not replicate ACL users, so the control plane keeps each password's SHA-256 (the form Valkey stores) and rebuilds the users on a promoted or restored master.
**Ruled out:** a user per disk (it would restrict writes, not reads), an instance per disk until per-disk access is really needed.

## 20. Several disks are folders of the one share at /Volumes/Floppy

**Active, 17/10/2026.** A team's disks appear as `/Volumes/Floppy/<disk>`: one SMB mount at the fixed mount point, each disk its own JuiceFS mount inside the VM, exported as a subfolder of the `Floppy` share. The folder is the disk's display name, part of every path CapCut keeps, so a disk is never renamed; names are ASCII (letters, digits, space, dot, hyphen, underscore) until a spike shows accented paths survive Samba and CapCut. The JuiceFS name is a slug derived once and never shown. Samba runs as an unprivileged user and the share's root is `0555`, so nothing can be created beside the disks: as root, Samba ignores the mode (`spike/perdisk.py`, t24). A disk the person may not see is not mounted, so its folder is absent; a read-only disk is mounted `--read-only`.
**Ruled out:** one SMB mount per disk at `/Volumes/<disk>` (a `-1` suffix risk and a mount to keep alive for each), the slug as folder name, renaming a disk.

## 21. TLS to the central Valkey

**Active, 17/10/2026.** Macs reach the central Valkey across the internet with their own password, for writes and for replication: the connection is TLS (`rediss://`), on the master and on its replica in the datacenter. SMB stays plain only because it never leaves the Mac.
**Ruled out:** plain Valkey over the WAN, a VPN or tunnel (a system extension or administrator rights on every Mac).

## 22. Storage in two modes, with team, per-person or per-Mac keys

**Superseded by #24, 17/10/2026.** Supersedes #4; the core still speaks only plain S3. **Manual**, for any S3: the admin picks one team key (kept in the volumes' format, rotated by hand when someone leaves, #18) or one key per person (made at the provider, limited to the bucket by a policy, pasted when inviting; removing the person or losing one of their Macs means deleting that one key, no rotation). With a team key, bucket versioning is recommended (old versions expiring after 7 days, the key denied version deletion), and the control plane checks and shows whether it is on. **Managed**: an optional, isolated adapter (OVH, AWS, MinIO, Garage to start) creates the bucket and gives each Mac (or person) its own key, deleted at the provider on revocation; where a provider cannot scope keys, it rotates a team key by itself. The hosted service always uses managed; mode and scope are fixed once a disk exists. Per-person and per-Mac keys leave the format empty and need JuiceFS to mount with a key from the environment (the AWS SDK's chain when the format holds none, or a patch), and a team rotation without the key in argv needs patch 0003. Details in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md#storage-modes).
**Ruled out:** provider APIs in the core (adapters are optional and outside it), per-person or per-Mac keys stored in the format (every replica would carry every key), managed-only (it would drop any S3 without an adapter), versioning without a key that is denied version deletion (a copied key would erase the versions too).

## 23. Per-disk access: one Valkey instance and one bucket per disk

**Active, 17/10/2026.** Each person has, on each disk, no access, read-only or read-write; a device inherits its person's. A replica copies its whole instance and a key opens its whole bucket, so access is enforced where the data is: every disk has its own Valkey instance and its own bucket. A device gets a replica and a mount only for the disks it may see. On each of those instances it has one Valkey user (its one password, #7), whose ACL is the access: read-only is `+@read` plus what replication and the patch's probe need, read-write adds `+@write +@transaction`, EVAL and EVALSHA; the exact lists are in `spike/perdisk.py`. A read-only mount writes nothing to the metadata, and a mount without `--read-only` on a read-only user fails at start. Lowering access on a mounted disk makes writes fail with an I/O error until the app remounts it read-only, so the app remounts; raising it needs only the ACL. Measured in `spike/perdisk.py`: per disk, a replica takes 12-15 MB and a mount 34-39 MB on the device; three disks of 10,000 files sync in 1.8 s with `repl-diskless-sync-delay 0`.
**Ruled out:** one instance per organization with a user per disk (the replica would still carry every disk), access enforced only in the app, a patch to JuiceFS (the ACL and `--read-only` are enough).

## 24. Storage: a key per disk (manual) or per device and disk (managed)

**Active, 17/10/2026.** Supersedes #22, with the same two modes; the core still speaks only plain S3. **Manual**, for any S3: the admin creates one bucket and one key per disk, limited to that bucket, and pastes them; the key is in the disk's format. No access is enforced at the storage too, since a device never receives the format of a disk it may not see; read-only is enforced on the disk and its metadata, but the key in the format can write the bucket, so bucket versioning is recommended (old versions expiring after 7 days, the key denied version deletion). Removing someone who could see a disk opens the rotation window for that disk's key alone (#18). **Managed**: the adapter (Garage built; OVH, AWS, MinIO to come) creates each disk's bucket and gives each device one key, granted read or read-write per bucket and changed in place when access changes; revocation deletes the key at the provider. The format then holds no key and each mount brings its own from the environment (verified on Garage). The hosted service always uses managed; the mode is fixed once a disk exists. Patch 0003 keeps a manual key rotation out of argv.
**Ruled out:** a team key for every bucket (anyone could read the raw blocks of a disk they may not see), per-person keys in manual mode (the admin would edit provider policies at every access change, which Floppy cannot check), provider APIs in the core.

## 25. A meta service runs the disks' Valkey instances, reached through one TLS port

**Proposed, 17/10/2026.** The control plane does not start containers: a `meta` service in the compose runs one Valkey instance per disk, created, reconfigured and stopped on the control plane's request, with its data on the service's volume. Devices reach every instance on one public TLS port, where the reverse proxy (Traefik, as Dokploy ships it) routes by the name in the TLS handshake, `<disk>.meta.<domain>`, with a wildcard certificate. It becomes active once a spike shows that JuiceFS and a Valkey replica both send that name; if the replica does not, each disk gets its own port instead.
**Ruled out:** the Docker socket in the control plane, Valkey's numbered databases (a replica copies them all), a port per disk as the first choice (a firewall rule for every new disk).
