#!/bin/bash
# Renders the marks in design/mark/ into the images the app bundles: the menu
# bar template icons (src-tauri/icons/tray/) and the app icon set. Run it after
# a mark changes; the output is committed.
set -euo pipefail
cd "$(dirname "$0")/.."
MARKS=../design/mark
RSVG=$(command -v rsvg-convert) || { echo "render-marks: needs rsvg-convert (brew install librsvg)" >&2; exit 1; }

# Template images, black plus alpha: macOS tints them. tray-icon draws a menu
# bar image 18 pt tall, so the 24x16 mark is padded to 24x18 to keep its real
# size, and rendered at 2x.
tray=src-tauri/icons/tray
mkdir -p "$tray"
for name in connected blink unreachable off starting starting-blink; do
  sed -e 's/viewBox="0 0 24 16"/viewBox="0 -1 24 18"/' \
      -e 's/width="24" height="16"/width="48" height="36"/' \
      "$MARKS/menubar-$name.svg" | "$RSVG" -f png -o "$tray/$name.png"
done

# The app icon on the macOS grid: the 116-unit squircle becomes 824 of 1024 px.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed -e 's/width="128" height="128" viewBox="0 0 124 124"/width="1024" height="1024" viewBox="-10.078 -10.078 144.156 144.156"/' \
  "$MARKS/app-icon.svg" > "$tmp/icon.svg"
"$RSVG" -f png -o "$tmp/icon.png" "$tmp/icon.svg"
pnpm tauri icon "$tmp/icon.png" -o "$tmp/icons" >/dev/null
for f in 32x32.png 128x128.png 128x128@2x.png icon.icns icon.ico icon.png; do
  cp "$tmp/icons/$f" src-tauri/icons/
done
