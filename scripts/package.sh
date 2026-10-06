#!/bin/bash
# Builds the zip handed to a tester: Lima, the fluix command, the VM
# files and a filled-in config. The zip holds secrets: share it privately.
# Usage: scripts/package.sh <config.env> [output dir, default dist/]
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077 # the zip holds the bucket keys

LIMA_VERSION=2.2.1
config="${1:?usage: scripts/package.sh <config.env> [output dir]}"
out="${2:-dist}"
for key in BUCKET_URL ACCESS_KEY SECRET_KEY SMB_PASSWORD; do
  grep -q "^$key=." "$config" || { echo "$config: $key is empty" >&2; exit 1; }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pkg="$work/fluix"
mkdir -p "$pkg/lima"

tarball="lima-${LIMA_VERSION}-Darwin-arm64.tar.gz"
base="https://github.com/lima-vm/lima/releases/download/v${LIMA_VERSION}"
curl -fsSL -o "$work/$tarball" "$base/$tarball"
(cd "$work" && curl -fsSL "$base/SHA256SUMS" | grep " $tarball\$" | shasum -a 256 -c - >/dev/null)
tar -xzf "$work/$tarball" -C "$pkg/lima" bin/limactl libexec share/lima

cp -R macos/bin macos/vm macos/install.sh "$pkg/"
install -m 600 "$config" "$pkg/config.env"

mkdir -p "$out"
zip="$(cd "$out" && pwd)/fluix.zip"
rm -f "$zip"
(cd "$work" && zip -qry "$zip" fluix)
echo "$zip"
