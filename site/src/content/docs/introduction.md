---
title: Introduction
description: What it is, who it is for, and what it does not do.
---

%name% is a shared remote disk for video teams on macOS. It shows up in the Finder as **%name%**, like an external drive, and the files live in S3 object storage that you choose. A local cache on each Mac makes the clips you have already opened as fast as a local SSD.

:::note[Status]
Today %name% is a test package for macOS, tried on OVH Object Storage in Milan, with one disk per Mac. The team version these pages describe (one shared disk, the menu bar app, sign-in in the browser) is proven in a local prototype and being built.
:::

## Who it is for

Small teams that edit video together and want one place for the footage: the same files on every Mac, without a sync folder and without a per-seat subscription. The person editing does not need to know anything about buckets or databases: they open the Finder and work.

## What it does

- **Mounts a disk** at `%volume%`. Any program opens files from it directly, and the path stays the same, so projects that reference clips by full path keep finding them.
- **Streams from S3.** Files are stored as 1 MB blocks. Playing or seeking fetches only the blocks of that point, and during playback a short readahead fetches what comes next.
- **Keeps a cache** on the Mac's SSD. What you open stays there, and **Make available** preloads a whole project before you start editing. **Free up space** gives the room back.
- **Shares one disk per team.** The file tree lives in a central Valkey, and each Mac keeps a replica of it: folders open without waiting for the network, and a file saved on one Mac shows up on the others at once.

## What it does not do

- **It does not save offline.** Without a network the disk stays browsable and the clips in the cache play, but saving, renaming and deleting fail with "read-only file system" until the connection is back. Clips that are not in the cache cannot be read.
- **It has no per-folder permissions.** Everyone in a team sees and can delete every file. A trash (7 days by default) and a replica of the bucket cover mistakes.
- **It is not a backup.** The bucket should be replicated to a second provider; see [Self-hosting](/self-hosting/).

## Requirements

- A Mac with Apple Silicon and macOS 13 or later, with [Homebrew](https://brew.sh).
- No administrator rights and no kernel extension: %name% runs its filesystem in a small Linux VM. [How it works](/how-it-works/) explains why.
- A team that hosts its own control plane, Valkey and S3 bucket, or uses a hosted one.

Next: [install on a Mac](/install/).
