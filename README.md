# Floppy

**English** · [Italiano](README.it.md)

A shared remote disk for video teams on macOS: it shows up in the Finder as a regular volume, the files live on S3 object storage, and a local cache makes the clips you already touched as fast as a local SSD.

## Why it exists

Editing video from a cloud bucket does not work with a plain network mount: a timeline reads scattered pieces of large files, and every read pays the latency to the datacenter. Streaming filesystems with a local cache (LucidLink, SpaceFS) solve this, at a per-seat price. Floppy builds the same thing from open source parts on storage you pick: [JuiceFS](https://juicefs.com) splits files into blocks on S3 and caches them on each Mac. Self-hosted, it has no per-seat price: a team pays the storage and one small metadata server, however many people use it.

On macOS JuiceFS would need macFUSE's kernel extension, which means booting every Mac into recovery mode. Floppy runs JuiceFS in a small Linux VM ([Lima](https://lima-vm.io), Apple's Virtualization framework) and shares it to the Mac over SMB on a private link: no kernel extension, no administrator rights.

```
Finder  ->  SMB (private Mac-VM link)  ->  Samba + JuiceFS in a Lima VM  ->  S3 bucket
                                            cache on the Mac's SSD           metadata: SQLite in the VM (test)
                                                                                       or PostgreSQL (shared)
```

## Install

Floppy ships as a Homebrew cask: the menu bar app with the engine inside, so the install downloads nothing but the app itself.

```bash
brew install --cask essedev/floppy/floppy
```

The tap (`essedev/homebrew-floppy`) is not published yet. The app is built with `make app` (`scripts/build-app.sh`), which needs Docker running because the patched JuiceFS is built and the Debian packages for the VM are fetched in containers. It produces `dist/app/Floppy.app` (about 555 MB: Lima, the Debian image, JuiceFS with Floppy's patches from `spike/juicefs-replica`, the Debian packages) and the DMG the cask downloads (about 410 MB). On first run the app installs the engine into `~/.floppy` and prepares the disk; after an upgrade it offers the engine's update. `scripts/release-app.sh` publishes a release and the cask (`packaging/homebrew`).

Until the team version configures the disk from the sign-in, testers get a zip built from a filled-in configuration, with the same engine:

```bash
cp macos/config.env.example my-test.env   # bucket, keys, SMB_PASSWORD
scripts/package.sh my-test.env            # creates dist/floppy.zip
```

The zip (about 445 MB) also holds the bucket keys: share it privately. On the Mac, unpack it and run:

```bash
bash floppy/install.sh
```

It needs Apple Silicon and macOS 13 or later, takes about 90 seconds, and the disk appears in the Finder as **Floppy**.

## Usage

```bash
~/.floppy/bin/floppy status          # VM, disk, Mac free space, cache in use
~/.floppy/bin/floppy warmup <path>   # make a project available offline-fast
~/.floppy/bin/floppy free <path>     # drop it from the cache
~/.floppy/bin/floppy stop | start
~/.floppy/bin/floppy uninstall       # removes VM, cache and agent
```

A launchd agent checks every 2 minutes that the VM, JuiceFS and Samba are running and that the disk is mounted, and empties the cache if the Mac runs low on space. The cache size is computed from the Mac's free space at every setup.

## Status

Early: a test package for macOS, tried on OVH Object Storage in Milan, with the metadata in SQLite inside each VM, so every Mac has its own disk. The team version is being built: the control plane (`server/`, sign-in, people, devices, disks, key rotation, storage modes) and its admin web (`web/`) work end to end on a local stack; the menu bar app (`app/`) has its interface but is not connected yet; the test package already runs the patched JuiceFS, and the rest of the engine for the team version (a Valkey replica in each VM, disks as folders) is next. Measurements, decisions and alternatives are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), plans in [docs/ROADMAP.md](docs/ROADMAP.md).

## License

[MIT](LICENSE)
