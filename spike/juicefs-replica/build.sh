#!/usr/bin/env bash
# Builds the patched juicefs (JuiceFS v1.4.1 + 0001-*.patch, reads from a
# local replica) for linux/arm64 and linux/amd64 with Docker buildx.
#
#   spike/juicefs-replica/build.sh [dist-dir]   # default: spike/juicefs-replica/dist
#
# Output: <dist>/juicefs-linux-arm64, <dist>/juicefs-linux-amd64 and their
# SHA256SUMS. `juicefs version` ends in "-replica". The clone of v1.4.1 and the
# Go modules are fetched from the network at build time.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
dist="${1:-$here/dist}"
mkdir -p "$dist"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

docker buildx build --platform linux/arm64,linux/amd64 --target out \
  --output "type=local,dest=$out" "$here"
for arch in arm64 amd64; do
  install -m 0755 "$out/linux_$arch/juicefs" "$dist/juicefs-linux-$arch"
done
(cd "$dist" && shasum -a 256 juicefs-linux-arm64 juicefs-linux-amd64 > SHA256SUMS)
cat "$dist/SHA256SUMS"
