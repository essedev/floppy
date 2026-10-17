#!/bin/bash
# Builds the zip handed to a tester: the engine (scripts/build-engine.sh, the
# same one Floppy.app carries) plus a filled-in config. The zip holds secrets:
# share it privately. It stays until the cask replaces it (ROADMAP).
# Usage: scripts/package.sh <config.env> [output dir, default dist/]
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077 # the zip holds the bucket keys

config="${1:?usage: scripts/package.sh <config.env> [output dir]}"
out="${2:-dist}"
for key in BUCKET_URL ACCESS_KEY SECRET_KEY SMB_PASSWORD; do
  grep -q "^$key=." "$config" || { echo "$config: $key is empty" >&2; exit 1; }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pkg="$work/floppy"
scripts/build-engine.sh "$pkg" >/dev/null
rm -f "$pkg/BUILD" # only the app reads it
install -m 600 "$config" "$pkg/config.env"

mkdir -p "$out"
zip="$(cd "$out" && pwd)/floppy.zip"
rm -f "$zip"
(cd "$work" && zip -qry "$zip" floppy)
echo "$zip"
