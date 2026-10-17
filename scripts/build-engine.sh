#!/bin/bash
# Assembles the engine as it goes onto a Mac, with no secrets in it: Lima, the
# floppy command, the VM files with the Debian image, the patched JuiceFS and
# the Debian packages, and macos/install.sh, which installs it into ~/.floppy.
# The app build (scripts/build-app.sh) puts it inside Floppy.app; the tester
# zip (scripts/package.sh) adds a config.env next to it.
# Usage: scripts/build-engine.sh <output dir, created empty>
# Downloads (Lima, the Debian image) are kept in dist/cache, verified on
# every use. Needs Docker running: JuiceFS is built and the packages fetched
# in containers.
set -euo pipefail
cd "$(dirname "$0")/.."

LIMA_VERSION=2.2.1
out="${1:?usage: scripts/build-engine.sh <output dir>}"
[ ! -e "$out" ] || [ -z "$(ls -A "$out")" ] || { echo "$out is not empty" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is needed to build JuiceFS and fetch the Debian packages" >&2; exit 1; }
cache=dist/cache
mkdir -p "$out/lima" "$cache"

# Lima: the official release, checked against its SHA256SUMS. limactl is
# signed ad hoc with the virtualization entitlement; it is copied as is and
# never signed again, or the entitlement would be lost.
tarball="lima-${LIMA_VERSION}-Darwin-arm64.tar.gz"
base="https://github.com/lima-vm/lima/releases/download/v${LIMA_VERSION}"
sums=$(curl -fsSL "$base/SHA256SUMS" | grep " $tarball\$") || { echo "no checksum for $tarball" >&2; exit 1; }
if ! (cd "$cache" && echo "$sums" | shasum -a 256 -c - >/dev/null 2>&1); then
  curl -fsSL -o "$cache/$tarball" "$base/$tarball"
  (cd "$cache" && echo "$sums" | shasum -a 256 -c - >/dev/null)
fi
tar -xzf "$cache/$tarball" -C "$out/lima" bin/limactl libexec share/lima
codesign -d --entitlements - "$out/lima/bin/limactl" 2>/dev/null | grep -q com.apple.security.virtualization \
  || { echo "limactl lacks the virtualization entitlement" >&2; exit 1; }

cp -R macos/bin macos/vm macos/install.sh "$out/"

# Debian image: the one Lima's own template pins, verified with its digest.
template="$out/lima/share/lima/templates/_images/debian-13.yaml"
image_url=$(grep -oE 'https://[^"]*genericcloud-arm64[^"]*\.qcow2' "$template" | head -1)
image_digest=$(grep -A2 "$image_url" "$template" | grep -oE 'sha512:[0-9a-f]+')
[ -n "$image_url" ] && [ -n "$image_digest" ] || { echo "Debian image not found in $template" >&2; exit 1; }
image_cache="$cache/debian-${image_digest#sha512:}.qcow2"
image_ok() { echo "${image_digest#sha512:}  $1" | shasum -a 512 -c - >/dev/null 2>&1; }
if ! image_ok "$image_cache"; then
  curl -fsSL -o "$image_cache.part" "$image_url"
  image_ok "$image_cache.part" || { echo "Debian image digest mismatch" >&2; exit 1; }
  mv "$image_cache.part" "$image_cache"
fi
cp "$image_cache" "$out/vm/debian.qcow2"
echo "$image_digest" > "$out/vm/debian.qcow2.digest"

# JuiceFS for the VM: the patched build (spike/juicefs-replica), arm64 like the
# rest of the engine (install.sh accepts only Apple Silicon). build.sh reuses
# its last binary while the patches, the upstream version and the recipe are
# unchanged. Run in the VM's Debian, the binary must report the version the
# guest install expects and this patch set.
patches=spike/juicefs-replica
jdist="$patches/dist"
"$patches/build.sh" "$jdist" >/dev/null
(cd "$jdist" && grep ' juicefs-linux-arm64$' SHA256SUMS | shasum -a 256 -c - >/dev/null)
install -m 755 "$jdist/juicefs-linux-arm64" "$out/vm/guest/juicefs"
juicefs_version=$(sed -n 's/^JUICEFS_VERSION=//p' macos/vm/guest/guest-install.sh)
patchset=$(cat "$patches"/*.patch | shasum -a 256 | cut -c1-8)
reported=$(docker run --rm --platform linux/arm64 -v "$(cd "$out" && pwd)/vm/guest/juicefs:/juicefs:ro" \
  debian:trixie /juicefs version)
case "$reported" in
  "juicefs version $juicefs_version+"*"-floppy.$patchset") ;;
  *) echo "juicefs reports \"$reported\", expected $juicefs_version with patch set $patchset" >&2; exit 1 ;;
esac

# Debian packages for the VM as a flat local repository, for the PACKAGES that
# guest-install.sh installs. An empty dpkg status makes apt fetch the whole
# dependency closure, not only what the container lacks: in the VM apt then
# takes just what the image is missing.
packages=$(sed -n 's/^PACKAGES=(\(.*\))$/\1/p' macos/vm/guest/guest-install.sh)
[ -n "$packages" ] || { echo "PACKAGES not found in guest-install.sh" >&2; exit 1; }
mkdir -p "$out/vm/guest/debs"
docker run --rm --platform linux/arm64 -e PACKAGES="$packages" \
  -v "$(cd "$out" && pwd)/vm/guest/debs:/out" debian:trixie bash -euc '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends apt-utils >/dev/null
    : > /tmp/empty-status
    mkdir -p /tmp/archives/partial
    apt-get install -y -qq --download-only --no-install-recommends \
      -o Dir::State::status=/tmp/empty-status -o Dir::Cache::archives=/tmp/archives $PACKAGES >/dev/null
    cp /tmp/archives/*.deb /out/
    cd /out && apt-ftparchive packages . > Packages' >/dev/null

# BUILD names this engine: the hash of every file in it. The app compares it
# with the one it installed last to know when the engine needs updating.
(cd "$out" && find . -type f ! -name BUILD | LC_ALL=C sort | tr '\n' '\0' | xargs -0 shasum -a 256 \
  | shasum -a 256 | cut -c1-16) > "$out/BUILD"
echo "$out"
