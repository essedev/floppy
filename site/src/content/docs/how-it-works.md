---
title: How it works
description: The VM, the SMB link, the cache, the local copy of the file tree, and the three kinds of traffic.
---

A plain network mount does not work for editing video from a bucket: a timeline reads scattered pieces of large files, and every read pays the latency to the datacenter. %name% builds a streaming filesystem with a local cache from open source parts.

:::note[Status]
This page describes the team version: one disk per team, the file tree in a central Valkey with a replica in each Mac. It is proven in a local prototype and being built. Today's test package keeps the file tree in SQLite inside each VM, so every Mac has its own disk.
:::

## Three places

- **Each Mac** runs the menu bar app, the `%slug%` command that drives everything, and a small Linux VM.
- **The control plane** hands out access and configuration: accounts, devices, volumes, and one Valkey user per Mac. It is a web service the team hosts, or a hosted one.
- **Storage** is a central [Valkey](https://valkey.io) for the file tree and an S3 bucket for the contents, in the same region.

## On the Mac

[JuiceFS](https://juicefs.com) does the filesystem work: it splits files into 1 MB blocks on S3 and keeps the file tree in a database. On macOS it would need macFUSE's kernel extension, which means booting every Mac into Recovery to allow it. %name% runs JuiceFS inside a Linux VM ([Lima](https://lima-vm.io), on Apple's Virtualization framework) and shares it to the Mac over SMB.

- The SMB share listens only on the private link between the Mac and its VM. No port is exposed to the network.
- The VM keeps a replica of the file tree, which the central Valkey updates as every change happens.
- The cache lives on the Mac's SSD, inside the VM's disk. Its ceiling is computed on the Mac side, from the Mac's free space, because the VM cannot see it.
- A background agent checks every two minutes that the VM, JuiceFS and Samba are running and the disk is mounted.

## Two patches on JuiceFS

%name% runs JuiceFS 1.4.1 with two patches of its own, kept until they can go upstream:

- **The file tree from the local replica.** JuiceFS reads names, attributes, extended attributes and the list of blocks of each file from the replica in its VM, and writes to the central Valkey. A read waits for the replica to have the Mac's own last write, so you always see what you just saved. A write runs JuiceFS's own logic on what it read from the replica, then one script on the central Valkey checks that nothing changed and applies it: one round trip instead of three to six.
- **Whole blocks while playing.** The macOS SMB client reads ahead on its own, in 128 KB pieces, out of order. Inside a sequential stream the patch fetches whole blocks instead of ranges, so the same block is not downloaded twice.

## Three kinds of traffic

- **File data**: 1 MB blocks between the VM and the S3 bucket, with a 4 MB readahead during playback. The heavy traffic, and the only one the cache saves.
- **File tree**: writes from the VM to the central Valkey, one round trip each, and the stream of changes back to the replica in every VM. In normal operation reads never leave the Mac.
- **Control**: sign-in, configuration and heartbeat over HTTPS between the app and the control plane.

The control plane is not on the data path. If it goes down, mounted disks keep working; what waits is signing in, adding Macs and revoking access.

### Why the file tree has to be local

The Finder asks a lot. Through Samba, listing a folder of 300 files costs about 2,700 file system operations: lookups of `._` names, extended attributes for the Finder. With the file tree 15 ms away, each one is a round trip: that listing took 75-117 s. With the replica in the VM it takes 0.20-0.30 s.

Writes still cross the network: creating a file from the Finder costs about nine of them. Creating 100 small files takes 13.7-17 s at 15 ms.

## When the network drops

The JuiceFS mount works in one of three modes:

- **Replica**, the normal one: reads from the VM, writes to the central Valkey.
- **Master**, when the replica lags more than 2 seconds behind or has lost its link: reads go to the central Valkey too, slower but current.
- **Offline**, when the central Valkey cannot be reached: a clear network error, or 10 seconds without any answer. A slow answer does not count. The disk stays browsable from the replica and the clips in the cache play; saving, renaming and deleting fail at once with "read-only file system" until the connection is back.

A clip that is not in the cache cannot be read offline. Today that read waits for retries instead of failing at once.

## Credentials

Each Mac gets its own Valkey user, used for its writes and for its replica's stream. The S3 key is not handed to the Mac: JuiceFS keeps it inside the volume's metadata, on the central Valkey and in every replica, so whoever can read the metadata can reach the bucket. Revoking a Mac deletes its Valkey user and closes its connections, which stops both; rotating the S3 key afterwards shuts out any copy of the old one.

## Measured performance

On OVH Object Storage in Milan, with the test package (4 MB blocks), from a home line with 170 Mbps down:

| | |
|---|---|
| Seek in a clip already in the cache | 0-3 ms |
| Seek in a clip never opened | 0.9 s on average, 1.9 s at most |
| Reading a clip not in the cache | 12.1 MB/s, against 7.5 MB/s for a 60 Mbps clip |
| Copying 2.7 GB to the disk | 113 s, the line's limit |
| VM memory on the Mac | 1,035 MB |

In the local prototype, with 1 MB blocks, the 4 MB readahead and both patches, on a simulated 100 Mbps link with 25 ms to S3 (to be repeated on OVH):

| | |
|---|---|
| Reading a clip not in the cache | 11.6 MB/s, 93% of the line |
| Seek in a clip never opened | 0.38 s on average |
| First read after a jump while scrubbing | 0.40 s on average |
| The same cold read on a 1 Gbps link | 103 MB/s |

How each number was measured is in [ARCHITECTURE.md](%repo%/blob/main/docs/ARCHITECTURE.md#measured-performance).
