#!/bin/bash
# Writes a release's notes from the Conventional Commits between two refs, on stdout, in
# Markdown: what changed for the people who install the app (features, fixes, performance),
# then the install line and the comparison link. Adapted from Relay's scripts/release-notes.sh.
# Usage: scripts/release-notes.sh <to-ref> [from-ref]   (from-ref: the previous tag by default)
# docs, test, build, chore, refactor and style commits stay out: they change nothing for the
# person who installs the app. Parsing in plain bash: BSD awk on macOS has no match(..., array).
set -euo pipefail

REPO="${RELEASE_NOTES_REPO:-essedev/floppy}"
TO="${1:?usage: release-notes.sh <to-ref> [from-ref]}"
FROM="${2:-}"
[ -n "$FROM" ] || FROM="$(git describe --tags --abbrev=0 "${TO}^" 2>/dev/null || true)"
if [ -n "$FROM" ]; then range="$FROM..$TO"; else range="$TO"; fi

feat=(); fixes=(); perf=()
while IFS= read -r subject; do
  [[ "$subject" =~ ^([a-z]+)(\(([^\)]*)\))?(!)?:[[:space:]]+(.*)$ ]] || continue
  type="${BASH_REMATCH[1]}" scope="${BASH_REMATCH[3]}" bang="${BASH_REMATCH[4]}" desc="${BASH_REMATCH[5]}"
  line="- "
  [ -n "$scope" ] && line="- **$scope**: "
  [ "$bang" = "!" ] && line="${line}**Breaking:** "
  line="$line$desc"
  case "$type" in
    feat) feat+=("$line") ;;
    fix) fixes+=("$line") ;;
    perf) perf+=("$line") ;;
    *) [ "$bang" = "!" ] && feat+=("$line") ;;  # a breaking change is news whatever its type
  esac
done < <(git log --no-merges --format='%s' "$range")

out=""
section() { # <title> <line>...
  local title="$1" item
  shift
  [ "$#" -eq 0 ] && return
  [ -n "$out" ] && out+=$'\n'
  out+="### $title"$'\n'
  for item in "$@"; do out+="$item"$'\n'; done
}
section "Features" ${feat[@]+"${feat[@]}"}
section "Fixes" ${fixes[@]+"${fixes[@]}"}
section "Performance" ${perf[@]+"${perf[@]}"}
[ -n "$out" ] || out="_Maintenance release._"$'\n'

out+=$'\n'"Install: \`brew install --cask essedev/floppy/floppy\` · update: \`brew upgrade --cask floppy\`"$'\n'
[ -n "$FROM" ] && out+=$'\n'"**Full Changelog**: https://github.com/$REPO/compare/$FROM...$TO"$'\n'
printf '%s' "$out"
