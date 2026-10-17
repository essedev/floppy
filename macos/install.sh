#!/bin/bash
# Installs Floppy from this unpacked package into ~/.floppy (or FLOPPY_HOME)
# and sets up the VM. Run with: bash install.sh
# With a config.env in the package (the test zip) it mounts the single-user
# volume; without one (the app's bundle) it installs the team version, whose
# disks come after sign-in through `floppy disks apply`.
set -euo pipefail
cd "$(dirname "$0")"

[ "$(uname -s)" = Darwin ] || { echo "Floppy needs macOS." >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Floppy needs a Mac with Apple Silicon." >&2; exit 1; }
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 13 ] || { echo "Floppy needs macOS 13 or later." >&2; exit 1; }

FLOPPY_HOME="${FLOPPY_HOME:-$HOME/.floppy}"
export FLOPPY_HOME
# Over an existing install this is an update: it keeps the config, the VM and
# the volume. A fresh start is `floppy uninstall` first.
if [ -f "$FLOPPY_HOME/installed" ]; then
  echo "Updating the existing Floppy install (your files stay as they are)."
else
  echo "New Floppy install."
fi
mkdir -p "$FLOPPY_HOME"
rm -rf "${FLOPPY_HOME:?}/lima" "${FLOPPY_HOME:?}/bin" "${FLOPPY_HOME:?}/vm"
cp -R lima bin vm "$FLOPPY_HOME/"
# A log grown by an earlier failed attempt is of no use and can be huge.
if [ -f "$FLOPPY_HOME/lima.log" ] && [ "$(du -m "$FLOPPY_HOME/lima.log" | cut -f1)" -gt 100 ]; then
  : > "$FLOPPY_HOME/lima.log"
fi
# Keep the config of a previous install: it names the volume this Mac uses.
if [ -f config.env ] && [ ! -f "$FLOPPY_HOME/config.env" ]; then
  install -m 600 config.env "$FLOPPY_HOME/config.env"
  # Empty VOLUME_NAME: one volume per Mac, named after the user plus a random
  # suffix, so the same package can go to several people on one bucket.
  if grep -q '^VOLUME_NAME=$' "$FLOPPY_HOME/config.env"; then
    user=$(id -un | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-' | cut -c1-30)
    name="floppy-${user}-$(openssl rand -hex 3)"
    sed -i '' "s/^VOLUME_NAME=$/VOLUME_NAME=$name/" "$FLOPPY_HOME/config.env"
  fi
fi
# Files downloaded from Slack or a browser are quarantined, and macOS would
# refuse to run the unsigned binaries.
xattr -dr com.apple.quarantine "$FLOPPY_HOME" 2>/dev/null || true

"$FLOPPY_HOME/bin/floppy" setup
if [ -f "$FLOPPY_HOME/config.env" ]; then
  echo "Floppy is in the Finder sidebar. Commands: $FLOPPY_HOME/bin/floppy"
fi
