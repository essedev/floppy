---
name: floppy
description: Use when the user talks about Floppy or their team's shared disk in /Volumes/Floppy, its disks, folders, projects or video files on it, making something available offline or freeing up space on the Mac, who changed, renamed or deleted a file, getting something back from the trash, inviting or removing people, who can see which disk, their Macs or devices, the storage keys or the audit log. Covers the two MCP servers, `floppy` (the team) and `floppy-mac` (this Mac).
---

# Floppy

Floppy is a shared remote disk for teams on macOS. Each disk is a folder in `/Volumes/Floppy` on every Mac allowed to see it; the files live on S3 storage and are copied to a Mac when read. The people using it edit video (CapCut, Premiere): they are not technical, so answer in their words, not JuiceFS's or Samba's.

## Two servers, never confused

| Server | Where it runs | What it does |
|---|---|---|
| `floppy` | The team's control plane, at `https://<team address>/mcp/`, signed in through the browser | The team: disks, people, access, devices, storage keys, audit log, the activity of a disk |
| `floppy-mac` | This Mac, the Floppy app's binary (`floppy-app mcp`) | This Mac: the disks' state, make available offline, free up space, this Mac's activity, the trash, undo a rename, start |

The control plane never reaches a Mac, so nothing about this Mac's copies is on `floppy`, and nothing about people or access is on `floppy-mac`. If a server is missing, say which and how to add it (below) instead of improvising.

## Start every task by asking who and where

- `floppy` → `whoami`: an **admin** sees every tool; a **member** sees only their disks (`list_my_disks`), their devices and a disk's activity. Do not promise a member what only an admin can do: tell them to ask an admin.
- `floppy-mac` → `status`: whether Floppy runs, where the disk is mounted (`mount_point`, normally `/Volumes/Floppy`), each team disk with `id`, `folder`, `finderPath`, `access` (`read` or `write`), `state`, and the space (`cache_used_mb`, `cache_limit_mb`, `mac_free_mb`).

## Paths

- Paths given to `floppy-mac` are the Mac's, absolute, under the mount point: `/Volumes/Floppy/<disk folder>/<path>`. Get the disk folder from `status` (`disks[].finderPath`), never guess it, and never use `/Volumes/Floppy-1`.
- Activity and trash carry a path **inside the disk as the VM names it**: a character the Finder forbids is one in U+F001-F027 (a Finder name `A / B` is `A : B` on the Mac and `A` U+F022 `B` in the VM). `floppy-mac` adds `finderPath` (or `finderFolder`) with the Mac's path: use that one to read or open the file, and show the user the Finder's name.
- You can read and list files on the disk with your own file tools: they are ordinary files under `/Volumes/Floppy`. Reading a file that is not on the Mac yet downloads it: prefer listing, and ask before reading large video files.

## Common tasks

**"Make this project available offline" / "I'm going on a trip"**: `status`, check the folder is under a disk's `finderPath`, compare its size (your file tools) with the space left for copies (`cache_limit_mb - cache_used_mb`), then `make_available` with the folder. It waits until the whole folder is copied. If it does not fit, say so: copying more than the limit pushes out the oldest copies.

**"My Mac is full" / "free up space"**: `free_up_space` on the folders the user names or agrees to. Nothing is deleted: the files stay on the disk and download again when opened. Never free a folder the user is editing now (`this_mac_activity` shows `open` files).

**"Who deleted / changed / renamed X?"**: `floppy` → `disk_activity` with the disk's id and `path` set to part of the name (works for admins and members on disks they see), or `floppy-mac` → `team_activity`. Answer with who and when, in the user's time zone.

**"Get X back"**: `floppy-mac` → `list_trash`, find the entry by name, folder and `deleted_at` (an entry with `replaced: true` is an older version a save replaced), confirm with the user when several match, then `restore_from_trash` with the disk's id (null for the single-user volume) and the entry ids. It never overwrites: a taken name comes back as "name (ripristinato)". Entries expire after the disk's `trash_days`.

**"I renamed the wrong folder"**: `this_mac_activity`, find the `renamed` event, `undo_rename` with its `disk`, `path` and `to`.

**"The disk is gone from the Finder"**: `status`. If `stopped` is true or the disk is not mounted, offer `start` and run it only on the user's yes. Never try to unmount, remount or restart anything else: a disk busy with a large copy looks dead and is not.

**Admins: a new colleague**: `invite_person` with their email, role and the disks they should see (`list_disks` for the ids); they get a mail. **Someone leaves**: `remove_person` (sessions end, their Macs are revoked); then `get_key_status`, and tell the admin when a disk's key has an open window to rotate in the admin web. **A lost Mac**: `revoke_device`.

**Admins: who sees what**: `list_access`, then `set_access` per person and disk (`none`, `read`, `write`), applied live to their Macs.

**Admins: a new disk**: `create_disk` works in managed storage only; it starts `creating`, so call `get_disk` until `ready` or `failed`. In manual storage a disk needs its bucket's key: send the admin to the admin web (Dischi).

## Rules

- **Ask before anything destructive or wide**: `remove_person`, `delete_disk`, `revoke_device`, lowering someone's access to `none`, freeing a whole disk. Name exactly what will happen and wait for a yes. `delete_disk` removes the disk from every Mac; its objects stay at the provider.
- **No secrets in the chat.** S3 keys, the SMTP password and rotations with a new key are entered in the admin web, never through you: if the user pastes one, tell them it is now in this conversation and to rotate it.
- **Errors are answers**: a tool result with `isError` carries `code` and `message` (`forbidden`, `not_found`, `validation`, `insufficient_scope`, `not_on_disk`, `not_mounted`, `signed_out`). Read it and fix the call or tell the user; do not retry blindly. `insufficient_scope` means the connection was granted less: the user reconnects (`/mcp` in Claude Code) and ticks more on the consent page.
- **The admin web** is at the team's address; the user's connections to Claude are listed there under "Claude", where they can disconnect them.

## Connecting

The admin web's "Claude" page shows the commands for this team. In short, in Terminal:

```sh
curl -fsS https://<team address>/api/v1/mcp/setup.sh | sh
```

It adds `floppy` (`claude mcp add --transport http --scope user floppy https://<team address>/mcp/`), `floppy-mac` when the app is installed (`claude mcp add --scope user floppy-mac -- /Applications/Floppy.app/Contents/MacOS/floppy-app mcp`) and this skill. The first call to `floppy` opens the browser on the consent page: sign in with the work email and choose what Claude may do. On claude.ai, add a custom connector with the `/mcp/` address.
