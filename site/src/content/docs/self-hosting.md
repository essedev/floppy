---
title: Self-hosting
description: Run your own control plane, with a central Valkey and an S3 bucket.
---

Everything %name% needs runs on infrastructure you pick. It speaks only standard S3 and the Redis protocol, which Valkey implements: no provider API is part of the core.

:::note[Draft]
The control plane is being built. This page describes the planned setup; commands and variable names will follow the first release.
:::

## What you run

- **The control plane**: an API with a web admin, shipped as Docker Compose, with its own PostgreSQL for its tables. It holds organizations, people, devices and volumes, sends the sign-in links, and creates or deletes the Valkey users of the Macs. It holds no file data.
- **A central Valkey** for the file tree: one database per volume, written by JuiceFS, and a replica of it in every Mac's VM. This is the critical piece, see [Backups](#backups).
- **An S3 bucket** per organization, at any provider. Volumes are prefixes inside it.

## Control plane

```sh
git clone %repo%
cd %slug%/server
cp .env.example .env    # database URLs, S3 endpoint, SMTP
docker compose up -d
```

TODO: the `server/` directory, the variable names and the first-start flow (one organization created at first start) depend on the control plane's first release.

Mail goes through plain SMTP. Back up the control plane's PostgreSQL like any other service's database.

## Valkey

- **In the same region as the bucket.** Reads of the file tree come from the replica on each Mac, but every write is a round trip to the central Valkey: creating one file from the Finder costs about nine of them.
- **One instance per organization.** A replica copies a whole Valkey instance, not one database, so a Mac would receive every volume on its instance: organizations never share one.
- **Durable by configuration**: every write on disk before the answer (`appendfsync always`), a replica in the datacenter, and a daily `juicefs dump` kept outside Valkey.
- **One user per Mac**, created by the control plane, which needs an admin user on Valkey to do it. Besides reading and writing the file tree, a Mac's user runs scripts (`EVAL`, `EVALSHA`) for writes in one round trip and feeds its replica (`PSYNC`, `REPLCONF`). Revoking a Mac deletes its user and closes its connections.

If the central Valkey stops, mounted disks turn read-only: browsing and cached clips keep working from each Mac's replica, saving waits until it answers again. If the control plane stops, nothing changes for mounted disks.

## S3

- Any S3-compatible provider. Egress matters more than space for video: every clip that is not cached downloads again.
- One key per organization, stored in the volume metadata, never in a file on the Mac. Rotation is pasting a new key in the admin and deleting the old one at the provider; do it after revoking a Mac.
- Keep versioning and Object Lock off on the main bucket: JuiceFS deletes and compacts blocks, and both would get in the way or bill the deleted blocks.

## Backups

- **The file tree is the filesystem.** Without it the blocks on S3 cannot be put back together into files. Hence the replica in the datacenter and the daily `juicefs dump`.
- **The replicas on the Macs are not a backup**: they follow the central Valkey, deletions included.
- **JuiceFS is not a backup.** Replicate the bucket to a second provider, with versioning there.
- Closed projects can leave the disk and go to a separate bucket as normal files, readable without the file tree.
