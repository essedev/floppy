---
title: Introduction
description: What it is, who it is for, and what it does not do.
---

%name% is a shared remote disk for video teams on macOS. It shows up in the Finder as **%name%**, like an external drive, and the files live in S3 object storage that you choose. A local cache on each Mac makes the clips you have already opened as fast as a local SSD.

## Who it is for

Small teams that edit video together and want one place for the footage: the same files on every Mac, without a sync folder and without a per-seat subscription. The person editing does not need to know anything about buckets or databases: they open the Finder and work.

## What it does

- **Mounts a disk** at `%volume%`. Any program opens files from it directly, and the path stays the same, so projects that reference clips by full path keep finding them.
- **Streams from S3.** Files are stored as 4 MB blocks. Playing or seeking fetches only the blocks of that point, and prefetch downloads ahead during playback.
- **Keeps a cache** on the Mac's SSD. What you open stays there, and **Make available** preloads a whole project before you start editing. **Free up space** gives the room back.
- **Shares one disk per team.** The file tree lives in PostgreSQL, so every Mac sees the same files a moment after they are saved.

## What it does not do

- **It does not work offline.** The file tree is remote: without a network the disk cannot be used, even for cached clips.
- **It has no per-folder permissions.** Everyone in a team sees and can delete every file. A trash (7 days by default) and a replica of the bucket cover mistakes.
- **It is not a backup.** The bucket should be replicated to a second provider; see [Self-hosting](/self-hosting/).

## Requirements

- A Mac with Apple Silicon and macOS 13 or later, with [Homebrew](https://brew.sh).
- No administrator rights and no kernel extension: %name% runs its filesystem in a small Linux VM. [How it works](/how-it-works/) explains why.
- A team that hosts its own control plane, PostgreSQL and S3 bucket, or uses a hosted one.

Next: [install on a Mac](/install/).
