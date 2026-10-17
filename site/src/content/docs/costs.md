---
title: Costs and alternatives
description: What a self-hosted disk costs by team size, what the table leaves out, and how the category compares.
---

Self-hosted, %name% has no per-seat price: a team pays the S3 storage and one small server for the metadata, however many people use the disk. Adding a person costs nothing; adding a TB costs the storage.

## Storage

Any S3-compatible provider works. For video, egress matters more than space: every clip that is not in a Mac's cache is downloaded again.

| Provider | Space | Egress |
|---|---|---|
| OVH Object Storage Standard | about $8 per TB a month | free within Europe |
| Backblaze B2 | $6.95 per TB a month | free up to three times the stored data, then $0.01 per GB |
| Hetzner | €6.49 a month with 1 TB, then €8.70 per TB | 1 TB included, then €1 per TB |
| Cloudflare R2 | $15 per TB a month | free |

## By team size

Estimated monthly cost, from published prices. The metadata server (the central Valkey and its replica in the datacenter) adds a fixed €20-30.

| Active data | SpaceFS Teams, 3 people | SpaceFS Teams, 10 people | %name%, any team size | %name%'s bucket backup |
|---|---|---|---|---|
| 3 TB | $90 | $300 | about €40-55 | about €20 |
| 5 TB | $114-138 | $300 | about €55-70 | about €35 |
| 10 TB | $174-258 | $300 | about €95-110 | about €70 |

SpaceFS Teams costs $30 per person a month with 1 TB per person pooled, and $6-12 per extra 500 GB. The backup column is a copy of the bucket on a second provider (Backblaze B2), which a self-hosted disk needs; the commercial prices do not say whether they include one.

## What the table leaves out

The time to run it: keeping Valkey up and its backups checked, answering when a Mac does not mount, and carrying %name%'s JuiceFS patches to new JuiceFS releases. For a small team that time is the real price. It pays off with more people, more TB, or data that must stay on infrastructure the team controls.

## The category

Streaming file systems with a local cache are an established category. Prices and features as each vendor publishes them.

**LucidLink**, the industry standard: data streamed from object storage, a cache on each computer, the file tree replicated to every client.

- Per member a month: Starter $7 with 100 GB, Business $27 with 400 GB.
- Your own bucket on the Enterprise plan, about €45 per TB with the storage.
- Permissions per person and per folder.

**SpaceFS Teams**: the same architecture, with the app and support included.

- $30 per person a month, with 1 TB per person pooled.
- In private beta, for macOS 26.4 or later on Apple Silicon.
- Permissions per person and per folder.

**%name%**: the same design on JuiceFS, open source.

- No per-seat price: the storage and a fixed metadata server, on a bucket you pick.
- macOS 13 or later on Apple Silicon, no administrator rights.
- Permissions are all or nothing.

## Pick something else if

- **Your team works in one office.** A NAS on 10 GbE gives local speed and is simpler to run.
- **You need permissions per person or per folder.** LucidLink and SpaceFS have them.
- **You edit on Windows or Linux.** %name% is for macOS.
- **You work fully offline.** Without a network the disk is read-only: a local disk or a sync folder fits better.
- **You write thousands of small files at once.** Each new file is a round trip to the metadata server: 100 small files take 13.7-17 s at 15 ms.
