---
title: How it works
description: The VM, the SMB link, the cache, and the three kinds of traffic.
---

A plain network mount does not work for editing video from a bucket: a timeline reads scattered pieces of large files, and every read pays the latency to the datacenter. %name% builds a streaming filesystem with a local cache from open source parts.

## Three places

- **Each Mac** runs the menu bar app, the `%slug%` command that drives everything, and a small Linux VM.
- **The control plane** hands out access and configuration: accounts, devices, volumes, and the database roles. It is a web service the team hosts.
- **Storage** is a PostgreSQL database for the file tree and an S3 bucket for the contents, in the same region.

## On the Mac

[JuiceFS](https://juicefs.com) does the filesystem work: it splits files into 4 MB blocks on S3 and keeps the file tree in a database. On macOS it would need macFUSE's kernel extension, which means booting every Mac into Recovery to allow it. %name% runs JuiceFS inside a Linux VM ([Lima](https://lima-vm.io), on Apple's Virtualization framework) and shares it to the Mac over SMB.

- The SMB share listens only on the private link between the Mac and its VM. No port is exposed to the network.
- The cache lives on the Mac's SSD, inside the VM's disk. Its ceiling is computed on the Mac side, from the Mac's free space, because the VM cannot see it.
- A background agent checks every two minutes that the VM, JuiceFS and Samba are running and the disk is mounted.

## Three kinds of traffic

- **File data**: 4 MB blocks between the VM and the S3 bucket. The heavy traffic, and the only one the cache saves.
- **Metadata**: the file tree between the VM and PostgreSQL. Small and frequent; JuiceFS caches attribute lookups for about a second.
- **Control**: sign-in, configuration and heartbeat over HTTPS between the app and the control plane.

The control plane is not on the data path. If it goes down, mounted disks keep working; what waits is signing in, adding Macs and revoking access.

## Credentials

Each Mac gets its own PostgreSQL role. The S3 key is not handed to the Mac: JuiceFS keeps it inside the volume's metadata, so whoever can read the metadata can reach the bucket, and whoever loses the role loses both. Revoking a Mac drops its role at once; rotating the S3 key afterwards closes the window in which a copied key still works.

## Measured performance

Measured on OVH Object Storage in Milan, from a 170 Mbps home line:

| | |
|---|---|
| Seek in a clip already in the cache | 3 ms |
| Seek in a clip never opened | 0.9 s (max 1.9 s) |
| Cold playback | 12.1 MB/s, against 7.5 MB/s for a 60 Mbps clip |
| VM memory on the Mac | 1,035 MB |
