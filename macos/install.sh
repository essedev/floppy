#!/bin/bash
# Installs Fluix from this unpacked package into ~/.fluix and
# mounts the disk. Run with: bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

[ "$(uname -s)" = Darwin ] || { echo "Fluix needs macOS." >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Fluix needs a Mac with Apple Silicon." >&2; exit 1; }
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 13 ] || { echo "Fluix needs macOS 13 or later." >&2; exit 1; }

FLUIX_HOME="$HOME/.fluix"
mkdir -p "$FLUIX_HOME"
rm -rf "$FLUIX_HOME/lima" "$FLUIX_HOME/bin" "$FLUIX_HOME/vm"
cp -R lima bin vm "$FLUIX_HOME/"
install -m 600 config.env "$FLUIX_HOME/config.env"
# Files downloaded from Slack or a browser are quarantined, and macOS would
# refuse to run the unsigned binaries.
xattr -dr com.apple.quarantine "$FLUIX_HOME" 2>/dev/null || true

"$FLUIX_HOME/bin/fluix" setup
echo "Fluix is in the Finder sidebar. Commands: ~/.fluix/bin/fluix"
