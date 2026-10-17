---
title: Licenses
description: Which license covers which part, and the third-party software it ships.
---

%name% is open source. The code is in one public repository: [%repo%](%repo%).

## %name%'s own code

| Part | License |
|---|---|
| The menu bar app | MIT |
| The `%slug%` command and the VM setup | MIT |
| The control plane (API and web admin) | AGPL-3.0 |

The app and the command run on your Macs and can be reused anywhere. The control plane is AGPL-3.0: if you run a modified version as a service for others, you share your changes under the same license.

TODO: the repository's root `LICENSE` is MIT today; the AGPL-3.0 license file arrives with the control plane.

## What it ships and builds on

| Software | License |
|---|---|
| [JuiceFS](https://github.com/juicedata/juicefs) Community | Apache-2.0 |
| [Lima](https://github.com/lima-vm/lima) | Apache-2.0 |
| [Samba](https://www.samba.org) | GPL-3.0 |
| Debian | mostly free software licenses, per package |

## This website

Built with [Astro](https://astro.build) and [Starlight](https://starlight.astro.build) (MIT). Fonts: [Schibsted Grotesk](https://github.com/schibsted/schibsted-grotesk) and [Departure Mono](https://departuremono.com), both under the SIL Open Font License 1.1, served with their license files.
