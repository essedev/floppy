#!/bin/bash
# Installs or updates the drive inside the VM. Run as root from the directory
# that holds these files; safe to rerun.
set -euo pipefail
cd "$(dirname "$0")"

JUICEFS_VERSION=1.4.1

export DEBIAN_FRONTEND=noninteractive
if ! command -v smbd >/dev/null || ! command -v fusermount3 >/dev/null; then
  apt-get update -q
  apt-get install -y -q --no-install-recommends samba samba-vfs-modules fuse3 ca-certificates curl
fi

if ! juicefs version 2>/dev/null | grep -q "$JUICEFS_VERSION"; then
  arch=$(dpkg --print-architecture)
  curl -fsSL "https://github.com/juicedata/juicefs/releases/download/v${JUICEFS_VERSION}/juicefs-${JUICEFS_VERSION}-linux-${arch}.tar.gz" \
    | tar -xz -C /usr/local/bin juicefs
fi

install -d -m 700 /etc/fluix /var/lib/fluix /var/cache/fluix
install -m 600 config.env /etc/fluix/config.env
install -m 755 fluix-mount /usr/local/sbin/fluix-mount
install -m 644 smb.conf /etc/samba/smb.conf
install -m 644 fluix-mount.service /etc/systemd/system/fluix-mount.service
install -d /etc/systemd/system/smbd.service.d
printf '[Unit]\nRequires=fluix-mount.service\nAfter=fluix-mount.service\n[Service]\nRestart=on-failure\nRestartSec=3\n' \
  > /etc/systemd/system/smbd.service.d/fluix.conf

# shellcheck disable=SC1091
. /etc/fluix/config.env
id drive >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin drive
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a drive >/dev/null

systemctl disable --now nmbd 2>/dev/null || true
systemctl daemon-reload
systemctl enable fluix-mount smbd
systemctl restart fluix-mount smbd
