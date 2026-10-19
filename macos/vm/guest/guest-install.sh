#!/bin/bash
# Installs or updates the drive inside the VM. Run as root from the directory
# that holds these files; safe to rerun. With a config.env next to it, the
# single-user volume (floppy-mount); without, the team version: Samba over the
# share's immutable root, the disks added later by floppy-disks, the SMB
# password on stdin.
set -euo pipefail
cd "$(dirname "$0")"

JUICEFS_VERSION=1.4.1

# valkey-server: Debian 13's 8.1.1, the series of the meta service's 8.1.4
# (the spike's masters); a replica of the same series replicates from it.
# python3: floppy-activity; the Debian image has it, Samba does not depend on it.
PACKAGES=(samba samba-vfs-modules fuse3 valkey-server jq python3)

export DEBIAN_FRONTEND=noninteractive
if ! command -v smbd >/dev/null || ! command -v fusermount3 >/dev/null \
    || ! command -v valkey-server >/dev/null || ! command -v jq >/dev/null \
    || ! command -v python3 >/dev/null; then
  # The package ships the .debs as a local repository (an office DNS already
  # failed on a Debian host), so apt resolves against what the image has and
  # downloads nothing. deb.debian.org stays as a fallback.
  local_ok=false
  if [ -f debs/Packages ]; then
    echo "deb [trusted=yes] file:$PWD/debs ./" > /tmp/floppy-local.list
    # The copied files are private to the VM user (the zip is built with umask
    # 077): apt's _apt sandbox user could not read them.
    local_apt=(-o Dir::Etc::SourceList=/tmp/floppy-local.list -o Dir::Etc::SourceParts=-
      -o APT::Sandbox::User=root -o Acquire::Languages=none)
    if apt-get update -q "${local_apt[@]}" \
      && apt-get install -y -q --no-install-recommends "${local_apt[@]}" "${PACKAGES[@]}"; then
      local_ok=true
    else
      echo "guest-install: bundled packages failed, trying deb.debian.org" >&2
    fi
    rm -f /tmp/floppy-local.list
  fi
  if [ "$local_ok" = false ]; then
    apt-get update -q
    apt-get install -y -q --no-install-recommends "${PACKAGES[@]}" ca-certificates
  fi
fi

# The package ships the patched JuiceFS (spike/juicefs-replica, checked by
# scripts/package.sh): there is no release of it to download, and the upstream
# one lacks the patches. Without the file, an installed binary of the same
# upstream version stays.
if [ -f juicefs ]; then
  install -m 755 juicefs /usr/local/bin/juicefs
elif ! juicefs version 2>/dev/null | grep -q " $JUICEFS_VERSION+"; then
  echo "guest-install: juicefs $JUICEFS_VERSION is missing from the package" >&2
  exit 1
fi

# Debian starts its own Valkey on 6379 at install: replicas run as
# floppy-replica@<disk> instead.
systemctl disable --now valkey-server >/dev/null 2>&1 || true
systemctl mask valkey-server >/dev/null 2>&1 || true

install -d -m 700 /etc/floppy /var/lib/floppy /var/cache/floppy
install -m 755 floppy-disks /usr/local/sbin/floppy-disks
install -m 644 smb.conf /etc/samba/smb.conf
install -m 755 floppy-activity /usr/local/sbin/floppy-activity
install -m 644 floppy-activity.service /etc/systemd/system/floppy-activity.service
install -d /etc/systemd/system/smbd.service.d
id drive >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin drive
systemctl disable --now nmbd 2>/dev/null || true

if [ -f config.env ]; then
  install -m 600 config.env /etc/floppy/config.env
  install -m 755 floppy-mount /usr/local/sbin/floppy-mount
  install -m 644 floppy-mount.service /etc/systemd/system/floppy-mount.service
  printf '[Unit]\nRequires=floppy-mount.service\nAfter=floppy-mount.service\n[Service]\nRestart=on-failure\nRestartSec=3\n' \
    > /etc/systemd/system/smbd.service.d/floppy.conf
  # shellcheck disable=SC1091
  . /etc/floppy/config.env
  # shellcheck disable=SC2153 # set by config.env
  printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a drive >/dev/null
  systemctl daemon-reload
  systemctl enable floppy-mount smbd floppy-activity
  systemctl restart floppy-mount smbd floppy-activity
  exit 0
fi

# The team version.
if systemctl is-enabled --quiet floppy-mount 2>/dev/null; then
  echo "guest-install: this VM holds the single-user volume; the team version needs a VM of its own" >&2
  exit 1
fi
IFS= read -r smb_password || true
[ -n "$smb_password" ] || { echo "guest-install: the SMB password is expected on stdin" >&2; exit 1; }
printf '%s\n%s\n' "$smb_password" "$smb_password" | smbpasswd -s -a drive >/dev/null
install -m 644 floppy-disk@.service floppy-replica@.service /etc/systemd/system/
printf '[Service]\nRestart=on-failure\nRestartSec=3\n' > /etc/systemd/system/smbd.service.d/floppy.conf
# The replicas' TLS context needs a certificate of its own; masters do not
# check it. Made once, private to Valkey.
install -d -m 750 -o root -g valkey /etc/floppy-replica
install -d -m 755 /var/lib/floppy-replica
if [ ! -f /etc/floppy-replica/tls.key ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 36500 -subj "/CN=floppy-replica" \
    -keyout /etc/floppy-replica/tls.key -out /etc/floppy-replica/tls.crt 2>/dev/null
  chown root:valkey /etc/floppy-replica/tls.key /etc/floppy-replica/tls.crt
  chmod 640 /etc/floppy-replica/tls.key /etc/floppy-replica/tls.crt
fi
# The share's root: the VM's own ext4 folder, immutable (DECISIONS #20).
mkdir -p /mnt/drive
if [ "$(lsattr -d /mnt/drive | cut -c5)" != i ]; then
  chmod 0555 /mnt/drive
  chattr +i /mnt/drive
fi
systemctl daemon-reload
systemctl enable smbd floppy-activity
systemctl restart smbd floppy-activity
