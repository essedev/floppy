#!/usr/bin/env bash
# Builds the patched juicefs (JuiceFS v1.4.1 + the *.patch files here) for
# linux/arm64 and linux/amd64 with Docker buildx. The spike, the control
# plane's image and the tester zip (scripts/package.sh) all take it from here.
#
#   spike/juicefs-replica/build.sh [dist-dir]   # default: spike/juicefs-replica/dist
#
# Output: <dist>/juicefs-linux-arm64, <dist>/juicefs-linux-amd64, their
# SHA256SUMS and BUILD, the hash of the inputs (Dockerfile, with the upstream
# version, this script and the patches). When BUILD matches and the binaries
# match SHA256SUMS the build is skipped; delete BUILD to force one.
# `juicefs version` ends in "-floppy.<patchset>" (see Dockerfile). The clone of
# the upstream tag and the Go modules are fetched from the network at build time.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
dist="${1:-$here/dist}"
mkdir -p "$dist"

inputs=$(cd "$here" && cat Dockerfile build.sh ./*.patch | shasum -a 256 | cut -d' ' -f1)
if [ "$(cat "$dist/BUILD" 2>/dev/null)" = "$inputs" ] \
    && (cd "$dist" && shasum -a 256 -c SHA256SUMS >/dev/null 2>&1); then
  echo "juicefs: up to date in $dist" >&2
  cat "$dist/SHA256SUMS"
  exit 0
fi

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
rm -f "$dist/BUILD"
docker buildx build --platform linux/arm64,linux/amd64 --target out \
  --output "type=local,dest=$out" "$here"
for arch in arm64 amd64; do
  install -m 0755 "$out/linux_$arch/juicefs" "$dist/juicefs-linux-$arch"
done
(cd "$dist" && shasum -a 256 juicefs-linux-arm64 juicefs-linux-amd64 > SHA256SUMS)
echo "$inputs" > "$dist/BUILD"
cat "$dist/SHA256SUMS"
