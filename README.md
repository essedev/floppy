# Fluix

**English** · [Italiano](README.it.md)

A shared remote disk for video teams on macOS: it shows up in the Finder as a regular volume, the files live on S3 object storage, and a local cache makes the clips you already touched as fast as a local SSD.

## Why it exists

Editing video from a cloud bucket does not work with a plain network mount: a timeline reads scattered pieces of large files, and every read pays the latency to the datacenter. Streaming filesystems with a local cache (LucidLink, SpaceFS) solve this, at a per-seat price. Fluix builds the same thing from open source parts on storage you pick: [JuiceFS](https://juicefs.com) splits files into blocks on S3 and caches them on each Mac.

On macOS JuiceFS would need macFUSE's kernel extension, which means booting every Mac into recovery mode. Fluix runs JuiceFS in a small Linux VM ([Lima](https://lima-vm.io), Apple's Virtualization framework) and shares it to the Mac over SMB on a private link: no kernel extension, no administrator rights.

```
Finder  ->  SMB (private Mac-VM link)  ->  Samba + JuiceFS in a Lima VM  ->  S3 bucket
                                            cache on the Mac's SSD           metadata: SQLite in the VM (test)
                                                                                       or PostgreSQL (shared)
```

## Install

Fluix ships as a zip built from a filled-in configuration. Building it needs Docker running, because the Debian packages for the VM are fetched in a container.

```bash
cp macos/config.env.example my-test.env   # bucket, keys, SMB_PASSWORD
scripts/package.sh my-test.env            # creates dist/fluix.zip
```

The zip (about 445 MB) bundles Lima, the Debian image, JuiceFS and the Debian packages, so the install downloads nothing. It also holds the bucket keys: share it privately. On the Mac, unpack it and run:

```bash
bash fluix/install.sh
```

It needs Apple Silicon and macOS 13 or later, takes about 90 seconds, and the disk appears in the Finder as **Fluix**.

## Usage

```bash
~/.fluix/bin/fluix status          # VM, disk, Mac free space, cache in use
~/.fluix/bin/fluix warmup <path>   # make a project available offline-fast
~/.fluix/bin/fluix free <path>     # drop it from the cache
~/.fluix/bin/fluix stop | start
~/.fluix/bin/fluix uninstall       # removes VM, cache and agent
```

A launchd agent checks every 2 minutes that the VM, JuiceFS and Samba are running and that the disk is mounted, and empties the cache if the Mac runs low on space. The cache size is computed from the Mac's free space at every setup.

## Status

Early: a test package for macOS, tried on OVH Object Storage in Milan, with the metadata in SQLite inside each VM, so every Mac has its own disk. Sharing one disk across Macs needs PostgreSQL in `META_URL` and is the next step. A menu bar app is designed (`mockups/panel.html`) but not built. Measurements, decisions and alternatives are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), plans in [docs/ROADMAP.md](docs/ROADMAP.md).

## License

[MIT](LICENSE)
