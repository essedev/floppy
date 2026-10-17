#!/bin/sh
# Formats the volume on first start, mounts JuiceFS, then serves it over SMB.
# Both run until the container stops.
set -eu

: "${META_URL:?}" "${BUCKET_URL:?}" "${SMB_PASSWORD:?}"
mkdir -p /mnt/drive /cache

# FORMAT_OPTS applies only when the volume is created, e.g. --block-size 1024.
if ! juicefs status "$META_URL" >/dev/null 2>&1; then
  # shellcheck disable=SC2086 # FORMAT_OPTS is a list of flags
  juicefs format "$META_URL" "${VOLUME_NAME:-spike}" --storage s3 --bucket "$BUCKET_URL" \
    --trash-days "${TRASH_DAYS:-0}" ${FORMAT_OPTS:-}
fi

# shellcheck disable=SC2086 # MOUNT_OPTS is a list of flags
juicefs mount "$META_URL" /mnt/drive ${MOUNT_OPTS:-} \
  --bucket "$BUCKET_URL" \
  --cache-dir /cache --cache-size "${CACHE_SIZE_MB:-102400}" \
  --prefetch "${PREFETCH:-8}" --buffer-size "${BUFFER_SIZE_MB:-1024}" \
  --upload-limit "${UPLOAD_LIMIT_MBPS:-0}" --download-limit "${DOWNLOAD_LIMIT_MBPS:-0}" \
  --enable-xattr \
  --background --log /var/log/juicefs.log

# SMB_DOS_ATTRIBUTES=no stops storing Windows attributes (archive, hidden,
# read-only) in an xattr on every file: two metadata writes less per file, at
# the cost of the Finder's Locked box and chflags hidden not being kept.
if [ "${SMB_DOS_ATTRIBUTES:-yes}" = no ]; then
  sed -i 's/^\[global\]$/[global]\n  store dos attributes = no\n  map archive = no\n  map hidden = no\n  map system = no\n  map readonly = no/' /etc/samba/smb.conf
fi

id drive >/dev/null 2>&1 || useradd -M -s /usr/sbin/nologin drive
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a drive >/dev/null

exec smbd --foreground --no-process-group --debug-stdout
