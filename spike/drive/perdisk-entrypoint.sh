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
# - ROOT_MODE=immutable: /mnt/drive is an ext4 folder with the immutable flag
#   (chattr +i), which stops root too while the share stays read-write; the flag
#   is lifted only to add or remove a disk's folder. In the VM it is the root
#   file system's own folder; here a small ext4 image, since overlayfs has no flag.
# `/perdisk-entrypoint.sh folder add|remove <name>` adds or removes a disk's folder.
set -eu

root_mode=${ROOT_MODE:-chmod}
if [ "${1:-}" = folder ]; then
  case "$root_mode" in
    tmpfs) mount -o remount,rw /mnt/drive ;;
    immutable) chattr -i /mnt/drive ;;
  esac
  case "$2" in
    add) mkdir -p "/mnt/drive/$3" ;;
    remove) rmdir "/mnt/drive/$3" ;;
    *) echo "folder add|remove <name>" >&2; exit 2 ;;
  esac
  chmod 0555 /mnt/drive
  case "$root_mode" in
    tmpfs) mount -o remount,ro /mnt/drive ;;
    immutable) chattr +i /mnt/drive ;;
  esac
  exit 0
fi

: "${SMB_PASSWORD:?}"
mkdir -p /mnt/drive /cache
if ! mountpoint -q /mnt/drive; then
  case "$root_mode" in
    tmpfs)
      mount -t tmpfs -o size=1m,mode=0555 floppy-root /mnt/drive
      mount -o remount,ro /mnt/drive
      ;;
    immutable)
      [ -f /cache/root.ext4 ] || { truncate -s 8M /cache/root.ext4 && mkfs.ext4 -q /cache/root.ext4; }
      mount -o loop /cache/root.ext4 /mnt/drive
      chattr -i /mnt/drive # the image keeps the flag from an earlier start
      rmdir /mnt/drive/lost+found 2>/dev/null || true
      chmod 0555 /mnt/drive
      chattr +i /mnt/drive
      ;;
  esac
fi
chmod 0555 /mnt/drive 2>/dev/null || true

id drive >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin drive
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a drive >/dev/null
if [ "${SMB_FORCE_USER:-root}" != root ]; then
  sed -i "s/^\( *force user = \)root$/\1${SMB_FORCE_USER}/" /etc/samba/smb.conf
fi

exec smbd --foreground --no-process-group --debug-stdout
