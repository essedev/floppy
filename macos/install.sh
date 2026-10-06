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
# Over an existing install this is an update: it keeps the config, the VM and
# the volume. A fresh start is `fluix uninstall` first.
if [ -f "$FLUIX_HOME/config.env" ]; then
  echo "Updating the existing Fluix install (your files stay as they are)."
else
  echo "New Fluix install."
fi
mkdir -p "$FLUIX_HOME"
rm -rf "$FLUIX_HOME/lima" "$FLUIX_HOME/bin" "$FLUIX_HOME/vm"
cp -R lima bin vm "$FLUIX_HOME/"
# Keep the config of a previous install: it names the volume this Mac uses.
if [ ! -f "$FLUIX_HOME/config.env" ]; then
  install -m 600 config.env "$FLUIX_HOME/config.env"
  # Empty VOLUME_NAME: one volume per Mac, named after the user plus a random
  # suffix, so the same package can go to several people on one bucket.
  if grep -q '^VOLUME_NAME=$' "$FLUIX_HOME/config.env"; then
    user=$(id -un | tr 'A-Z' 'a-z' | tr -c 'a-z0-9\n' '-' | cut -c1-30)
    name="fluix-${user}-$(openssl rand -hex 3)"
    sed -i '' "s/^VOLUME_NAME=$/VOLUME_NAME=$name/" "$FLUIX_HOME/config.env"
  fi
fi
# Files downloaded from Slack or a browser are quarantined, and macOS would
# refuse to run the unsigned binaries.
xattr -dr com.apple.quarantine "$FLUIX_HOME" 2>/dev/null || true

"$FLUIX_HOME/bin/fluix" setup
echo "Fluix is in the Finder sidebar. Commands: ~/.fluix/bin/fluix"
