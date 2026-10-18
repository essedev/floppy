#!/bin/bash
# Releases the app: builds the DMG (scripts/build-app.sh), tags the commit,
# publishes a GitHub release with the DMG and notes from the commits since the previous tag
# (scripts/release-notes.sh), and writes the cask for it into the
# tap, essedev/homebrew-floppy. Everything it does is public: run it by hand.
# Usage: scripts/release-app.sh
# Signed with the stable self-signed identity of scripts/setup-signing.sh; an ad hoc
# release is SIGN_IDENTITY=- scripts/release-app.sh.
# The version is the one in app/src-tauri/tauri.conf.json, app/package.json
# and app/Cargo.toml, bumped together beforehand. A version already on GitHub
# but missing from the tap only updates the tap.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=essedev/floppy
TAP=essedev/homebrew-floppy
GH_ACCOUNT=essedev
SIGN_IDENTITY="${SIGN_IDENTITY:-Floppy Self-Signed}"

fail() { echo "release: $*" >&2; exit 1; }

version=$(sed -n 's/^  "version": "\(.*\)",$/\1/p' app/src-tauri/tauri.conf.json)
[ -n "$version" ] || fail "no version in app/src-tauri/tauri.conf.json"
grep -q "^  \"version\": \"$version\",$" app/package.json || fail "app/package.json is not at $version"
grep -q "^version = \"$version\"$" app/Cargo.toml || fail "app/Cargo.toml is not at $version"
tag="v$version"

# Two GitHub accounts on this Mac: the repo and the tap belong to essedev.
if [ "$(gh api user --jq .login 2>/dev/null || true)" != "$GH_ACCOUNT" ]; then
  gh auth switch --user "$GH_ACCOUNT" >/dev/null || fail "cannot switch gh to $GH_ACCOUNT"
fi
gh repo view "$TAP" >/dev/null 2>&1 \
  || fail "the tap $TAP does not exist: create it with Casks/floppy.rb from packaging/homebrew"
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || fail "not on main"
if ! git diff --quiet || ! git diff --cached --quiet; then fail "the working tree has changes"; fi

update_tap() { # <sha256>
  local tmp
  tmp=$(mktemp -d)
  git clone --quiet --depth 1 "https://github.com/$TAP.git" "$tmp/tap"
  mkdir -p "$tmp/tap/Casks"
  packaging/homebrew/render.sh "$version" "$1" > "$tmp/tap/Casks/floppy.rb"
  git -C "$tmp/tap" add Casks/floppy.rb
  if git -C "$tmp/tap" diff --cached --quiet; then
    echo "The tap already has floppy $version." >&2
  else
    git -C "$tmp/tap" commit --quiet -m "floppy $version"
    git -C "$tmp/tap" push --quiet
  fi
  rm -rf "$tmp"
}

tagged=false released=false
git rev-parse "$tag" >/dev/null 2>&1 && tagged=true
gh release view "$tag" --repo "$REPO" >/dev/null 2>&1 && released=true
if $tagged && $released; then
  # Published before, the tap left behind: take the sha256 from the asset.
  dl=$(mktemp -d)
  gh release download "$tag" --repo "$REPO" --pattern '*.dmg' --dir "$dl"
  update_tap "$(shasum -a 256 "$dl"/*.dmg | cut -d' ' -f1)"
  rm -rf "$dl"
  exit 0
elif $tagged || $released; then
  fail "$tag is half published (tag $tagged, release $released): sort it out by hand"
fi

# A stable identity: setup-signing.sh checks the certificate, the keychain in the search
# list, the unlock and the trust.
if [ "$SIGN_IDENTITY" != "-" ]; then
  bash scripts/setup-signing.sh >&2 \
    || fail "signing identity '$SIGN_IDENTITY' not ready (see above), or release ad hoc: SIGN_IDENTITY=- scripts/release-app.sh"
fi
dmg=$(SIGN_IDENTITY="$SIGN_IDENTITY" scripts/build-app.sh)
sha=$(shasum -a 256 "$dmg" | cut -d' ' -f1)

git push origin main
git tag -a "$tag" -m "Floppy $tag"
git push origin "$tag"
# The notes come from the Conventional Commits since the previous tag (trunk-based: no pull
# requests, so --generate-notes would only give the comparison link).
notes=$(mktemp)
scripts/release-notes.sh "$tag" > "$notes"
gh release create "$tag" "$dmg" --repo "$REPO" --title "Floppy $tag" --notes-file "$notes"
rm -f "$notes"
update_tap "$sha"
echo "Released Floppy $tag: brew install --cask essedev/floppy/floppy" >&2
