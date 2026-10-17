#!/bin/sh
# The per-disk client (spike/perdisk.py): Samba exports /mnt/drive as the one
# Floppy share, and each disk the person may see is a JuiceFS mount in a folder
# of it (DECISIONS #20). The disks are mounted and unmounted by perdisk.py.
#
# Nothing may be written at the top level, where it would land on the VM's disk.
# Two ways tried (t24):
# - ROOT_MODE=chmod: /mnt/drive is 0555. Samba running as root (SMB_FORCE_USER=root,
#   the product's smb.conf) bypasses it; as an unprivileged user (drive) it holds.
# - ROOT_MODE=tmpfs: /mnt/drive is a read-only tmpfs, remounted read-write only
#   to add or remove a disk's folder. The macOS SMB client then treats the whole
#   share as read-only, disks included (t14f): it does not work.
# `/perdisk-entrypoint.sh folder add|remove <name>` adds or removes a disk's folder.
set -eu

if [ "${1:-}" = folder ]; then
  if mountpoint -q /mnt/drive; then mount -o remount,rw /mnt/drive; fi
  case "$2" in
    add) mkdir -p "/mnt/drive/$3" ;;
    remove) rmdir "/mnt/drive/$3" ;;
    *) echo "folder add|remove <name>" >&2; exit 2 ;;
  esac
  chmod 0555 /mnt/drive
  if mountpoint -q /mnt/drive; then mount -o remount,ro /mnt/drive; fi
  exit 0
fi

: "${SMB_PASSWORD:?}"
mkdir -p /mnt/drive /cache
if [ "${ROOT_MODE:-chmod}" = tmpfs ] && ! mountpoint -q /mnt/drive; then
  mount -t tmpfs -o size=1m,mode=0555 floppy-root /mnt/drive
  mount -o remount,ro /mnt/drive
fi
chmod 0555 /mnt/drive 2>/dev/null || true

id drive >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin drive
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a drive >/dev/null
if [ "${SMB_FORCE_USER:-root}" != root ]; then
  sed -i "s/^\( *force user = \)root$/\1${SMB_FORCE_USER}/" /etc/samba/smb.conf
fi

exec smbd --foreground --no-process-group --debug-stdout
