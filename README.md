# Floppy

**English** · [Italiano](README.it.md)

A shared remote disk for video teams on macOS: it shows up in the Finder as a regular volume, the files live on S3 object storage, and a local cache makes the clips you already touched as fast as a local SSD.

## Why it exists

Editing video from a cloud bucket does not work with a plain network mount: a timeline reads scattered pieces of large files, and every read pays the latency to the datacenter. Streaming filesystems with a local cache (LucidLink, SpaceFS) solve this, at a per-seat price. Floppy builds the same thing from open source parts on storage you pick: [JuiceFS](https://juicefs.com) splits files into blocks on S3 and caches them on each Mac. Self-hosted, it has no per-seat price: a team pays the storage and one small metadata server, however many people use it.

On macOS JuiceFS would need macFUSE's kernel extension, which means booting every Mac into recovery mode. Floppy runs JuiceFS in a small Linux VM ([Lima](https://lima-vm.io), Apple's Virtualization framework) and shares it to the Mac over SMB on a private link: no kernel extension, no administrator rights.

```
Finder  ->  SMB (private Mac-VM link)  ->  Samba + JuiceFS in a Lima VM  ->  S3 bucket
                                            cache on the Mac's SSD           metadata: SQLite in the VM (test)
                                                                                       or Valkey, replica in the VM (team)
```

## Install

Floppy ships as a Homebrew cask: the menu bar app with the engine inside, so the install downloads nothing but the app itself.

```bash
brew install --cask essedev/floppy/floppy
```

The tap (`essedev/homebrew-floppy`) has no cask yet: the first release writes it. The app is built with `make app` (`scripts/build-app.sh`), which needs Docker running because the patched JuiceFS is built and the Debian packages for the VM are fetched in containers. It produces `dist/app/Floppy.app` (about 555 MB: Lima, the Debian image, JuiceFS with Floppy's patches from `spike/juicefs-replica`, the Debian packages) and the DMG the cask downloads (about 410 MB). On first run the app installs the engine into `~/.floppy` and prepares the disk; after an upgrade it offers the engine's update. `scripts/release-app.sh` publishes a release and the cask (`packaging/homebrew`).

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

In the team version, without a `config.env`, the menu bar app drives the same command: it writes the disks the person may see to `~/.floppy/disks.json`, pipes the secrets to `floppy disks apply`, and each disk appears as a folder of **Floppy**, read-only or read-write; `floppy disks clear` removes them all from a revoked Mac. The contract is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#team-disks).

## Try the team version on one Mac

`deploy/local/` runs a self-hosted instance on this Mac that a real install can use: the production compose plus Traefik with a throwaway CA, Garage in managed mode and console mail.

```bash
deploy/local/stack.sh up             # server and admin web at http://127.0.0.1:4583, admin admin@floppy.test
deploy/local/stack.sh mail <email>   # the last sign-in code or link sent to that address
deploy/local/stack.sh hosts          # after creating a disk: its name in the VM's /etc/hosts
deploy/local/stack.sh down           # stops it; the data stays in its volumes
deploy/local/e2e.py                  # the whole chain with a development VM in /tmp/fe2e
```

In the app, the server is `http://127.0.0.1:4583`. The disks' metadata (`<label>.meta.floppy.test`) and the S3 endpoint (`s3.floppy.test`) point to the Mac only inside the VM, through `stack.sh hosts`, to run again after each new disk. `e2e.py` signs a member in through the app's own code against a real VM and checks the disks in the Finder, read-only and read-write access, a second device, a lowered access and a revocation; its docstring has the setup.

## Status

Early: a test package for macOS, tried on OVH Object Storage in Milan, with the metadata in SQLite inside each VM, so every Mac has its own disk. The team version works end to end on a local stack (`deploy/local/`, its `e2e.py` run with a real VM): an admin invites from the web (`web/`), the person signs in from the menu bar app (`app/`), and the disks appear as folders of **Floppy** with per-disk access, through the control plane (`server/`), the meta service (`meta/`) behind Traefik and the engine. Next: the first self-hosted instance on Dokploy and the Homebrew release. Measurements, decisions and alternatives are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), plans in [docs/ROADMAP.md](docs/ROADMAP.md).

## License

[MIT](LICENSE)
