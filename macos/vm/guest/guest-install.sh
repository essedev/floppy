#!/bin/bash
# Installs or updates the drive inside the VM. Run as root from the directory
# that holds these files; safe to rerun.
set -euo pipefail
cd "$(dirname "$0")"

JUICEFS_VERSION=1.4.1

PACKAGES=(samba samba-vfs-modules fuse3)

export DEBIAN_FRONTEND=noninteractive
if ! command -v smbd >/dev/null || ! command -v fusermount3 >/dev/null; then
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
    apt-get install -y -q --no-install-recommends "${PACKAGES[@]}" ca-certificates curl
  fi
fi

# The package ships the binary (no GitHub download from an office network);
# the download stays as a fallback for a package without it.
if [ -f juicefs ]; then
  install -m 755 juicefs /usr/local/bin/juicefs
elif ! juicefs version 2>/dev/null | grep -q "$JUICEFS_VERSION"; then
  arch=$(dpkg --print-architecture)
  curl -fsSL "https://github.com/juicedata/juicefs/releases/download/v${JUICEFS_VERSION}/juicefs-${JUICEFS_VERSION}-linux-${arch}.tar.gz" \
    | tar -xz -C /usr/local/bin juicefs
fi

install -d -m 700 /etc/floppy /var/lib/floppy /var/cache/floppy
install -m 600 config.env /etc/floppy/config.env
install -m 755 floppy-mount /usr/local/sbin/floppy-mount
install -m 644 smb.conf /etc/samba/smb.conf
install -m 644 floppy-mount.service /etc/systemd/system/floppy-mount.service
install -d /etc/systemd/system/smbd.service.d
printf '[Unit]\nRequires=floppy-mount.service\nAfter=floppy-mount.service\n[Service]\nRestart=on-failure\nRestartSec=3\n' \
  > /etc/systemd/system/smbd.service.d/floppy.conf

# shellcheck disable=SC1091
. /etc/floppy/config.env
id drive >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin drive
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a drive >/dev/null

systemctl disable --now nmbd 2>/dev/null || true
systemctl daemon-reload
systemctl enable floppy-mount smbd
systemctl restart floppy-mount smbd
