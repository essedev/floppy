#!/usr/bin/env bash
# A throwaway CA and a wildcard certificate for *.meta.floppy.test, for the
# "metatls" profile (spike/metatls.py): Traefik terminates TLS with it and the
# Valkey replicas load it for their TLS context. Written to spike/meta-tls/,
# outside git; rerunning keeps existing files.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p meta-tls
cd meta-tls
[ -f .gitignore ] || printf '*\n' > .gitignore
if [ ! -f ca.crt ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=Floppy spike CA" \
    -keyout ca.key -out ca.crt 2>/dev/null
fi
if [ ! -f meta.crt ]; then
  openssl req -newkey rsa:2048 -nodes -subj "/CN=*.meta.floppy.test" \
    -keyout meta.key -out meta.csr 2>/dev/null
  printf 'subjectAltName=DNS:*.meta.floppy.test\nextendedKeyUsage=serverAuth,clientAuth\n' > meta.ext
  openssl x509 -req -in meta.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 30 \
    -extfile meta.ext -out meta.crt 2>/dev/null
  rm -f meta.csr meta.ext
fi
# Read by the Valkey containers' own user; a test CA with no other use.
chmod 0644 ca.crt meta.crt meta.key
echo "spike/meta-tls: ca.crt, meta.crt (*.meta.floppy.test), meta.key"
