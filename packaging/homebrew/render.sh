#!/bin/bash
# Prints the cask for one release: Casks/floppy.rb with its version and the
# DMG's sha256 filled in. With a third argument the download URL is replaced
# too, for an install from a local file before anything is published.
# Usage: packaging/homebrew/render.sh <version> <sha256> [url]
set -euo pipefail
cask="$(dirname "$0")/Casks/floppy.rb"
version="${1:?usage: render.sh <version> <sha256> [url]}"
sha="${2:?usage: render.sh <version> <sha256> [url]}"
url="${3:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not a version: $version" >&2; exit 1; }
[[ "$sha" =~ ^[0-9a-f]{64}$ ]] || { echo "not a sha256: $sha" >&2; exit 1; }

out=$(sed -E \
  -e "s|^  version \".*\"$|  version \"$version\"|" \
  -e "s|^  sha256 .*$|  sha256 \"$sha\"|" \
  "$cask")
if [ -n "$url" ]; then
  out=$(printf '%s\n' "$out" | sed -E "s|^  url \".*\"$|  url \"$url\"|")
fi
# A change in the cask's layout would make the substitutions no-ops.
if ! grep -q "^  version \"$version\"$" <<<"$out" || ! grep -q "^  sha256 \"$sha\"$" <<<"$out"; then
  echo "$cask: version or sha256 line not found" >&2
  exit 1
fi
if [ -n "$url" ]; then
  grep -qF "  url \"$url\"" <<<"$out" || { echo "$cask: url line not found" >&2; exit 1; }
fi
printf '%s\n' "$out"
