#!/usr/bin/env bash
# Sets up Floppy's self-signed code signing identity, stable across builds, so macOS sees the
# same app after an update and does not ask again for the device token in the Keychain.
# Idempotent; copied from Relay's scripts/setup-signing.sh.
#
# It creates a DEDICATED keychain in ~/.floppy-codesign (outside ~/.floppy, which uninstall
# removes, and outside the login keychain) holding a self-signed code signing certificate.
# macOS signs only with a trusted certificate, and setting the trust needs the admin password:
# that is the one step that cannot be automated (sudo). The script tries it non-interactively
# and otherwise prints the exact command to run once by hand.
set -euo pipefail

DIR="$HOME/.floppy-codesign"
KC="$DIR/floppy-codesign.keychain-db"
CERT="$DIR/floppy-selfsigned.cer"
PWFILE="$DIR/keychain.pw"
IDENTITY="Floppy Self-Signed"
SYSKC="/Library/Keychains/System.keychain"

mkdir -p "$DIR" && chmod 700 "$DIR"

# 1. Keychain and certificate (headless, idempotent). The system LibreSSL, not Homebrew's
# OpenSSL: macOS cannot import an OpenSSL 3+ PKCS#12 (its MAC check fails).
if [ ! -f "$KC" ] || ! security find-certificate -c "$IDENTITY" "$KC" >/dev/null 2>&1; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  cat > "$tmp/cfg" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = $IDENTITY
[v3]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF
  /usr/bin/openssl req -x509 -newkey rsa:2048 -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
    -days 3650 -nodes -config "$tmp/cfg" 2>/dev/null
  cp "$tmp/cert.pem" "$CERT"
  p12pass="$(/usr/bin/openssl rand -hex 12)"
  /usr/bin/openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -out "$tmp/cert.p12" \
    -name "$IDENTITY" -passout pass:"$p12pass" 2>/dev/null
  kcpass="$(/usr/bin/openssl rand -hex 16)"
  rm -f "$KC"
  security create-keychain -p "$kcpass" "$KC"
  security set-keychain-settings "$KC"                 # no lock on a timeout
  security unlock-keychain -p "$kcpass" "$KC"
  security import "$tmp/cert.p12" -k "$KC" -P "$p12pass" -T /usr/bin/codesign
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$kcpass" "$KC" >/dev/null
  ( umask 077; printf '%s' "$kcpass" > "$PWFILE" )
  chmod 600 "$PWFILE"
  echo "keychain and certificate created in $DIR"
else
  echo "keychain and certificate already in $DIR"
  security unlock-keychain -p "$(cat "$PWFILE")" "$KC"
fi

# 1b. Search list: codesign finds the identity through the keychain search list (its
# --keychain flag is not enough), so the dedicated keychain joins it, keeping the others.
if ! security list-keychains -d user | grep -qF -- "$KC"; then
  # `-s` REPLACES the whole search list: the existing entries go into a quoted array, so
  # paths with spaces survive.
  existing=()
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"   # trim leading spaces
    line="${line%\"}"; line="${line#\"}"       # strip surrounding quotes
    [ -n "$line" ] && existing+=("$line")
  done < <(security list-keychains -d user)
  security list-keychains -d user -s "${existing[@]}" "$KC"
  echo "keychain added to the search list"
fi

# 2. Trust (needs sudo, once).
if security find-identity -v -p codesigning "$KC" | grep -q "$IDENTITY"; then
  echo "OK: '$IDENTITY' is valid for code signing."
  exit 0
fi

echo "the certificate is not trusted yet (macOS needs that to sign)."
if sudo -n security add-trusted-cert -d -r trustRoot -p codeSign -k "$SYSKC" "$CERT" 2>/dev/null; then
  echo "trust added."
else
  cat <<MSG

  One command with your admin password, once. Run it yourself:

    sudo security add-trusted-cert -d -r trustRoot -p codeSign -k "$SYSKC" "$CERT"

  Then run again:  bash scripts/setup-signing.sh
  (to remove it later: sudo security remove-trusted-cert -d "$CERT")
MSG
  exit 2
fi

if security find-identity -v -p codesigning "$KC" | grep -q "$IDENTITY"; then
  echo "OK: '$IDENTITY' is valid for code signing."
else
  echo "trust added but the identity is not valid yet; run again or check by hand." >&2
  exit 3
fi
