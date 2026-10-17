#!/usr/bin/env bash
# Sets up the local Garage of server/docker-compose.yml (profile s3) for development and the
# integration tests: one node, a bucket, a key that may use it and a key that may not.
# Idempotent; keys and the RPC secret go into server/.env (outside git), never on screen.
#
#   docker/garage-setup.sh      # from server/
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
[ -f .env ] || { echo "server/.env is missing: copy .env.example first" >&2; exit 1; }

set_env() {  # set_env NAME VALUE: adds or replaces a line of .env
  local tmp
  tmp=$(mktemp)
  grep -v "^$1=" .env > "$tmp" || true  # no previous line is fine
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  mv "$tmp" .env
}
env_value() { grep "^$1=" .env | tail -1 | cut -d= -f2- || true; }

if [ -z "$(env_value FLOPPY_GARAGE_RPC_SECRET)" ]; then
  set_env FLOPPY_GARAGE_RPC_SECRET "$(openssl rand -hex 32)"
fi
docker compose --profile s3 up -d --wait garage

garage() { docker compose exec -T garage /garage "$@"; }

for _ in $(seq 1 30); do garage status >/dev/null 2>&1 && break; sleep 1; done
if garage layout show | grep -q "No nodes currently have a role"; then
  node_id=$(garage status | awk '/NO ROLE ASSIGNED/ {print $1; exit}')
  garage layout assign -z local -c 10G "$node_id" >/dev/null
  garage layout apply --version 1 >/dev/null
fi

bucket=floppy-dev
garage bucket info "$bucket" >/dev/null 2>&1 || garage bucket create "$bucket" >/dev/null

# make_key NAME PREFIX: creates the key once and stores its id and secret as PREFIX_*.
make_key() {
  local id out
  id=$(env_value "$2_ACCESS_KEY_ID")
  if [ -n "$id" ] && garage key info "$id" >/dev/null 2>&1; then return; fi
  out=$(garage key create "$1")
  set_env "$2_ACCESS_KEY_ID" "$(awk '/Key ID:/ {print $3}' <<<"$out")"
  set_env "$2_SECRET_KEY" "$(awk '/Secret key:/ {print $3}' <<<"$out")"
}
make_key floppy-dev FLOPPY_TEST_S3
make_key floppy-dev-second FLOPPY_TEST_S3_SECOND
make_key floppy-dev-denied FLOPPY_TEST_S3_DENIED
for prefix in FLOPPY_TEST_S3 FLOPPY_TEST_S3_SECOND; do
  garage bucket allow --read --write --owner "$bucket" --key "$(env_value "${prefix}_ACCESS_KEY_ID")" >/dev/null
done

set_env FLOPPY_TEST_S3_BUCKET "$bucket"
echo "Garage ready: http://127.0.0.1:4509 (bucket $bucket; keys in server/.env)"
