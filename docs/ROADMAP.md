# Roadmap

The principle is the one in [ARCHITECTURE.md](ARCHITECTURE.md#guiding-principle): build only what the previous step proved necessary. Work already done is in [CYCLES.md](CYCLES.md), the decisions in [DECISIONS.md](DECISIONS.md).

## 1. Data-path spike (next)

The metadata spike is closed (16 of 17 checks, CYCLES 6). What is left is the data path: over SMB the Mac reads a never-opened clip at about half the line (5.7 MB/s on a simulated 100 Mbps), and one read took 15 s. To try, one at a time, against targets set before measuring (cold read at least 80% of the line, cold seek under 0.4 s at 100 Mbps, no single read over 3 s): SMB multichannel (Samba advertising the VM's interface as RSS-capable so the Mac opens several connections), 1 MB blocks, more JuiceFS readahead. The I/O error seen in some cold reads (`t03`) belongs here.

## 2. Name

Floppy is chosen and the repository is renamed (DECISIONS #14). Left: check it directly on EUIPO and UIBM in classes 9 and 42, rename the GitHub repository and the URL that points to it (`site/src/brand.ts`, `docs/concepts/concept-d-onboarding.html`), and hand the tester a new zip before anyone uses the disk with CapCut projects. Domains are set aside for now.

## 3. Build

- **Design on paper**: the team architecture from [architecture.html](architecture.html) into ARCHITECTURE.md, and `docs/DATABASE_SCHEMA.md` for the control plane.
- **Engine**: the patched JuiceFS and a Valkey replica in the VM's offline package set, `floppy status --json`, the metadata URL from the app instead of a config file, the SMB password in the Keychain.
- **Control plane**: FastAPI and React, accounts with magic link sign-in, organizations, disks, one Valkey user per Mac, key rotation, audit log, Docker Compose for self-hosting.
- **App**: Tauri from the Portsage skeleton, built from the concepts (panel, settings, first run).
- **Distribution and first instance**: the Homebrew cask, then the first self-hosted instance on Dokploy with a central Valkey and S3 in Milan.

## 4. Pilot

One shared disk, one or two weeks of real work in CapCut, on the first instance. To measure: scrubbing on uncached clips, uploading footage, first opening of a folder with many videos, how the disk behaves when the line is busy or drops. Every problem noted with what and when: it decides what the app does next.

## 5. Alongside

- **The JuiceFS patch upstream**: propose the read replica and the optimistic transactions to JuiceFS, so the fork does not have to live forever.
- **Site**: Astro and Starlight in `site/` (DECISIONS #13), with the domain once there is one.
- **Comparison with SpaceFS**: their 7-day trial on the same project as the pilot.
- **At the end of the tests**: revoke the test S3 key and empty the test bucket.
