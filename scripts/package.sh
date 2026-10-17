#!/bin/bash
# Builds the zip handed to a tester: Lima, the floppy command, the VM
# files with the patched JuiceFS (built here) and a filled-in config. The zip
# holds secrets: share it privately.
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
docker info >/dev/null 2>&1 || { echo "Docker is needed to build JuiceFS and fetch the Debian packages" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pkg="$work/floppy"
mkdir -p "$pkg/lima"

tarball="lima-${LIMA_VERSION}-Darwin-arm64.tar.gz"
base="https://github.com/lima-vm/lima/releases/download/v${LIMA_VERSION}"
curl -fsSL -o "$work/$tarball" "$base/$tarball"
(cd "$work" && curl -fsSL "$base/SHA256SUMS" | grep " $tarball\$" | shasum -a 256 -c - >/dev/null)
tar -xzf "$work/$tarball" -C "$pkg/lima" bin/limactl libexec share/lima

cp -R macos/bin macos/vm macos/install.sh "$pkg/"

# Debian image: the one Lima's own template pins, verified with its digest.
template="$pkg/lima/share/lima/templates/_images/debian-13.yaml"
image_url=$(grep -oE 'https://[^"]*genericcloud-arm64[^"]*\.qcow2' "$template" | head -1)
image_digest=$(grep -A2 "$image_url" "$template" | grep -oE 'sha512:[0-9a-f]+')
[ -n "$image_url" ] && [ -n "$image_digest" ] || { echo "Debian image not found in $template" >&2; exit 1; }
curl -fsSL -o "$pkg/vm/debian.qcow2" "$image_url"
echo "${image_digest#sha512:}  $pkg/vm/debian.qcow2" | shasum -a 512 -c - >/dev/null
echo "$image_digest" > "$pkg/vm/debian.qcow2.digest"

# JuiceFS for the VM: the patched build (spike/juicefs-replica), arm64 like the
# rest of the package (install.sh accepts only Apple Silicon). build.sh reuses
# its last binary while the patches, the upstream version and the recipe are
# unchanged. Run in the VM's Debian, the binary must report the version the
# guest install expects and this patch set.
patches=spike/juicefs-replica
jdist="$patches/dist"
"$patches/build.sh" "$jdist" >/dev/null
(cd "$jdist" && grep ' juicefs-linux-arm64$' SHA256SUMS | shasum -a 256 -c - >/dev/null)
install -m 755 "$jdist/juicefs-linux-arm64" "$pkg/vm/guest/juicefs"
juicefs_version=$(sed -n 's/^JUICEFS_VERSION=//p' macos/vm/guest/guest-install.sh)
patchset=$(cat "$patches"/*.patch | shasum -a 256 | cut -c1-8)
reported=$(docker run --rm --platform linux/arm64 -v "$pkg/vm/guest/juicefs:/juicefs:ro" \
  debian:trixie /juicefs version)
case "$reported" in
  "juicefs version $juicefs_version+"*"-floppy.$patchset") ;;
  *) echo "juicefs reports \"$reported\", expected $juicefs_version with patch set $patchset" >&2; exit 1 ;;
esac

# Debian packages for the VM as a flat local repository. An empty dpkg status
# makes apt fetch the whole dependency closure, not only what the container
# lacks: in the VM apt then takes just what the image is missing.
packages=$(sed -n 's/^PACKAGES=(\(.*\))$/\1/p' macos/vm/guest/guest-install.sh)
[ -n "$packages" ] || { echo "PACKAGES not found in guest-install.sh" >&2; exit 1; }
mkdir -p "$pkg/vm/guest/debs"
docker run --rm --platform linux/arm64 -e PACKAGES="$packages" \
  -v "$pkg/vm/guest/debs:/out" debian:trixie bash -euc '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends apt-utils >/dev/null
    : > /tmp/empty-status
    mkdir -p /tmp/archives/partial
    apt-get install -y -qq --download-only --no-install-recommends \
      -o Dir::State::status=/tmp/empty-status -o Dir::Cache::archives=/tmp/archives $PACKAGES >/dev/null
    cp /tmp/archives/*.deb /out/
    cd /out && apt-ftparchive packages . > Packages' >/dev/null
install -m 600 "$config" "$pkg/config.env"

mkdir -p "$out"
zip="$(cd "$out" && pwd)/floppy.zip"
rm -f "$zip"
(cd "$work" && zip -qry "$zip" floppy)
echo "$zip"
