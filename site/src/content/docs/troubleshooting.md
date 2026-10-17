---
title: Troubleshooting
description: What to check when the disk is missing, slow, or the Mac is short of space.
---

The menu bar panel shows the state of the disk with its LED and a readout. Most problems show up there first.

## The disk is not in the Finder

- Open the panel. **Unreachable** means the Mac cannot reach the database or the bucket: check the network. The app retries every 30 seconds on its own, and **Reconnect** retries at once.
- **Off** means someone turned it off: choose **Turn on**.
- If the panel says **Connected** but the Finder shows nothing, look under Locations in the Finder sidebar: the disk is at `%volume%`.

## The disk shows up as %name%-1

macOS adds `-1` when the folder of a previous mount is still there. Projects that reference clips by full path then lose their files. Turn the disk off and on again from the panel: %name% mounts only on the free name.

TODO: the exact recovery steps depend on the app's first release.

## A clip stalls or the editor reports a read error

- A clip never opened needs about a second per seek; on a slow line more. Before editing, choose **Make available** on the project so it comes into the cache.
- macOS gives up on an SMB read after 30 seconds. %name% raises that limit for the VM's address only, in a marked block of `~/Library/Preferences/nsmb.conf`; if that block was removed, `~/.%slug%/bin/%slug% setup` writes it again.

## The Mac is running out of space

The cache ceiling is computed from the Mac's free space. When free space drops below the reserve, the background agent empties the cache and the panel shows **Mac almost full**. Free up space on the Mac, or **Free up space** on projects you are not editing.

## Diagnostics

**Send diagnostics** in the settings window sends the state, the latest logs of the command and of the VM, and the macOS version to your control plane, with a code to quote to your admin. From the Terminal:

```sh
~/.%slug%/bin/%slug% status
```

TODO: where the app installs the command (today `~/.%slug%`) may change with the app.
