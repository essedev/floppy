#!/bin/bash
# Builds Floppy.app with the engine inside and the DMG the Homebrew cask
# downloads (packaging/homebrew/floppy.rb). The engine is the one of
# scripts/build-engine.sh, in Contents/Resources/engine: the app installs it
# into ~/.floppy on first run and on updates (app/src-tauri/src/installer.rs).
# Usage: scripts/build-app.sh [output dir, default dist/app]
# SIGN_IDENTITY picks the code signing identity, ad hoc ("-") by default; releases use
# "Floppy Self-Signed" (scripts/setup-signing.sh), stable across versions. With
# NOTARY_PROFILE (a `xcrun notarytool store-credentials` profile) and a "Developer ID
# Application" identity, the app gets the hardened runtime, Apple's notarization and its
# ticket stapled: Gatekeeper then opens it without asking (DECISIONS #10).
# Needs Docker running (the engine), pnpm and Rust.
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:-dist/app}"
identity="${SIGN_IDENTITY:--}"
version=$(sed -n 's/^  "version": "\(.*\)",$/\1/p' app/src-tauri/tauri.conf.json)
[ -n "$version" ] || { echo "no version in app/src-tauri/tauri.conf.json" >&2; exit 1; }
mkdir -p "$out"
out=$(cd "$out" && pwd)
app="$out/Floppy.app"
dmg="$out/Floppy_${version}_aarch64.dmg"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
echo "Building the engine..." >&2
scripts/build-engine.sh "$work/engine" >/dev/null

echo "Building the app..." >&2
(cd app && pnpm install --frozen-lockfile >/dev/null && pnpm tauri build --bundles app >&2)
built=app/target/release/bundle/macos/Floppy.app
[ -d "$built" ] || { echo "the Tauri build left no $built" >&2; exit 1; }

rm -rf "$app" "$dmg"
ditto "$built" "$app"
ditto "$work/engine" "$app/Contents/Resources/engine"

# The bundle is signed as a whole, without --deep: the Mach-O files in the
# engine keep their own signatures, limactl its virtualization entitlement.
# Notarization wants every Mach-O signed by the same Developer ID with the
# hardened runtime and a timestamp, so they are signed again first, each with
# the entitlements it had.
if [ -n "${NOTARY_PROFILE:-}" ]; then
  case "$identity" in "Developer ID Application"*) ;; *)
    echo "NOTARY_PROFILE needs SIGN_IDENTITY set to a Developer ID Application identity" >&2; exit 1 ;;
  esac
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      ents="$work/$(basename "$f").entitlements"
      if codesign -d --entitlements - --xml "$f" >"$ents" 2>/dev/null && [ -s "$ents" ]; then
        codesign --force --options runtime --timestamp --entitlements "$ents" --sign "$identity" "$f"
      else
        codesign --force --options runtime --timestamp --sign "$identity" "$f"
      fi
    fi
  done < <(find "$app/Contents/Resources/engine" -type f -perm +111 -print0)
  codesign --force --options runtime --timestamp --sign "$identity" "$app"
else
  codesign --force --sign "$identity" "$app"
fi
codesign --verify --strict "$app"
codesign -d --entitlements - "$app/Contents/Resources/engine/lima/bin/limactl" 2>/dev/null \
  | grep -q com.apple.security.virtualization \
  || { echo "limactl lost its virtualization entitlement" >&2; exit 1; }

if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "Notarizing (a few minutes)..." >&2
  ditto -c -k --keepParent "$app" "$work/notarize.zip"
  xcrun notarytool submit "$work/notarize.zip" --keychain-profile "$NOTARY_PROFILE" --wait >&2
  xcrun stapler staple "$app" >&2
  spctl --assess --type execute -vv "$app" >&2 \
    || { echo "Gatekeeper refuses the notarized app" >&2; exit 1; }
fi

# A plain DMG with the app and a link to Applications, compressed with LZMA.
stage="$work/dmg"
mkdir -p "$stage"
ditto "$app" "$stage/Floppy.app"
ln -s /Applications "$stage/Applications"
hdiutil create -quiet -volname Floppy -srcfolder "$stage" -fs APFS -format ULMO "$dmg"

echo "app: $app ($(du -sh "$app" | cut -f1)), engine $(cat "$app/Contents/Resources/engine/BUILD")" >&2
echo "dmg: $dmg ($(du -h "$dmg" | cut -f1)), sha256 $(shasum -a 256 "$dmg" | cut -d' ' -f1)" >&2
echo "$dmg"
