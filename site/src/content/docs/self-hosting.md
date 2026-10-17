---
title: Self-hosting
description: Run your own control plane, with PostgreSQL and an S3 bucket.
---

Everything %name% needs runs on infrastructure you pick. It speaks only standard S3 and PostgreSQL: no provider API is part of the core.

:::note[Draft]
The control plane is being built. This page describes the planned setup; commands and variable names will follow the first release.
:::

## What you run

- **The control plane**: an API with a web admin, shipped as Docker Compose. It holds organizations, people, devices and volumes, sends the sign-in links, and creates or drops the database roles.
- **PostgreSQL** for the metadata: one database per volume, written by JuiceFS. This is the critical piece, see [Backups](#backups).
- **Valkey**, alongside the control plane. TODO: what it holds (sessions, rate limits, a job queue) is not decided yet.
- **An S3 bucket** per organization, at any provider. Volumes are prefixes inside it.

## Control plane

```sh
git clone %repo%
cd %slug%/server
cp .env.example .env    # database URLs, S3 endpoint, SMTP
docker compose up -d
```

TODO: the `server/` directory, the variable names and the first-start flow (one organization created at first start) depend on the control plane's first release.

Mail goes through plain SMTP. The control plane holds no file data: back up its own database like any other service.

## PostgreSQL

- Managed, in the same region as the bucket: every metadata miss is a network round trip, so latency matters more than size.
- Point-in-time recovery, plus a daily `juicefs dump` kept outside the database.
- The control plane needs an admin role that can create roles and databases. Each Mac gets its own role, with access only to its organization's databases.

## S3

- Any S3-compatible provider. Egress matters more than space for video: every clip that is not cached downloads again.
- One key per organization, stored in the volume metadata, never in a file on the Mac. Rotation is pasting a new key in the admin and deleting the old one at the provider.
- Keep versioning and Object Lock off on the main bucket: JuiceFS deletes and compacts blocks, and both would get in the way or bill the deleted blocks.

## Backups

- **The metadata database is the filesystem.** Without it the blocks on S3 cannot be put back together into files.
- **JuiceFS is not a backup.** Replicate the bucket to a second provider, with versioning there.
- Closed projects can leave the disk and go to a separate bucket as normal files, readable without the database.
