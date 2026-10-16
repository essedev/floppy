# Roadmap

The principle is the one in [ARCHITECTURE.md](ARCHITECTURE.md#guiding-principle): build only what the previous step proved necessary. Work already done is in [CYCLES.md](CYCLES.md).

## 1. Open questions

To settle before a pilot, because they change the choice:

- **How many people edit and how many active TB**: they decide between SpaceFS and Fluix.
- **Where they work**: same office (then NAS + sync to S3) or from different places (streaming filesystem).
- **What connection they have**: on FTTC the project does not hold up, on FTTH it does.
- **Data requirements**: if the footage belongs to clients, region and GDPR also decide on SpaceFS (region not stated).
- **Versioning of the production bucket**: off in the test, to be decided ([ARCHITECTURE.md](ARCHITECTURE.md#solution-juicefs-on-ovh)).
- To check: OVH managed PostgreSQL in Milan; 1-AZ or 3-AZ region for production; price of SpaceFS extra space on the Teams plan.

## 2. Test on a tester's Mac (in progress)

A tester runs on their Mac (macOS 27) the zip from `scripts/package.sh` rebuilt on 08/10/2026: a test bucket, a 1 GiB VM, `CACHE_SIZE_MB=15360`, so a cache up to 15 GB and a VM up to about 25 GB on the Mac. The VM's disk is fixed at creation: raising the cache ceiling beyond that size means reinstalling. Every install has its own disk, with the metadata in the VM: the test covers the disk on that Mac, with CapCut and that line, not sharing.

- **Status**: in progress, basically working. The test list: install, CapCut with an export to the Mac and to Fluix, reopening the project, sleep and restart of the Mac, a project larger than the cache, a network change, `fluix free`. Sleep was already tested during development, and that is where the `Fluix-1` fix came from; a real restart and a network change are on the tester.
- **To note with the tester**: what stutters, what errors out, how long uploads take.
- **Cold seeks**: try 1 MB blocks instead of 4 on a second volume in the same bucket. If it pays off, rebuild the zip.
- **At the end of the test**: revoke the test user's key and empty the test bucket, which holds only test volumes.

Open, to close before a pilot:

- **Next zip for the tester**: the Debian packages for Samba and FUSE are now in the zip and the install downloads nothing ([ARCHITECTURE.md](ARCHITECTURE.md#macos-package)). The tester's current zip still downloads them from `deb.debian.org`, and it predates the rename: the next one installs as Fluix, so the disk becomes `/Volumes/Fluix` and CapCut projects pointing at the old disk need their media relinked. Changes are batched into a single zip after the tester's feedback, instead of a reinstall halfway through the test.
- **SMB password in the mount URL**: it goes through `mount volume` and is visible in `ps` for a moment ([CONVENTIONS.md](CONVENTIONS.md#secrets)). It has to move to the macOS Keychain.
- **macOS 27**: not validated yet, the end of the tester's run will tell.

## 3. Pilot

One shared disk, one or two weeks of real work in CapCut. Setup estimated at about a day (a guess).

- Managed PostgreSQL in `META_URL`: the zip becomes the same for everyone, and whoever installs it sees the same disk.
- The two Finder Quick Actions instead of `fluix warmup` and `fluix free`.
- To measure: scrubbing on uncached clips, uploading footage, how CapCut behaves with slow files, first opening of a folder with many videos.
- To note: every time something goes wrong, what and when. It is the input for phase 6.

## 4. Comparison with SpaceFS

Alongside the pilot, the 7-day trial of SpaceFS Teams on the same project and the same test. If SpaceFS holds up, the price difference has to be weighed against the time it takes to run Fluix.

## 5. Team rollout

Only if the pilot holds and the comparison favors Fluix: install on the Macs in one script, backups (daily dump, replica on Backblaze), an alert if the database or the bucket stop answering, a page of procedures for whoever runs it.

## 6. Menu bar app

- **Now, without waiting for the pilot**: pick the [mockup](ARCHITECTURE.md#interface) variant and close the decisions it leaves open, then the Tauri skeleton from Portsage with the panel wired to the `fluix` command.
- **After the pilot**: only the features for observed problems, picked from the list in [ARCHITECTURE.md](ARCHITECTURE.md#interface).
