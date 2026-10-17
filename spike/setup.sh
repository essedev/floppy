#!/usr/bin/env bash
# Setup of the local S3: starts the containers, then Garage layout, bucket,
# access key and the simulated link. Secrets live in spike/.env (not in git).
# Safe to rerun: it only resets the simulated link.
#
# Simulated link: LATENCY_MS (default 25) to S3 and PG_LATENCY_MS (default 15,
# the round trip measured to OVH Milan) to PostgreSQL; bandwidth in
# docker-compose.yml (DOWNLOAD_LIMIT_MBPS, UPLOAD_LIMIT_MBPS).
set -euo pipefail
cd "$(dirname "$0")"

LATENCY_MS="${LATENCY_MS:-25}"
PG_LATENCY_MS="${PG_LATENCY_MS:-15}"
touch .env
if ! grep -q '^GARAGE_RPC_SECRET=' .env; then
  echo "GARAGE_RPC_SECRET=$(openssl rand -hex 32)" >> .env
fi
if ! grep -q '^SMB_PASSWORD=' .env; then
  echo "SMB_PASSWORD=$(openssl rand -hex 12)" >> .env
fi
if ! grep -q '^PG_PASSWORD=' .env; then
  echo "PG_PASSWORD=$(openssl rand -hex 16)" >> .env
fi
docker compose up -d --wait garage postgres toxiproxy

garage() { docker compose exec -T garage /garage "$@"; }
toxi() { docker compose exec -T toxiproxy /toxiproxy-cli "$@"; }

node_id=$(garage status | awk '/NO ROLE ASSIGNED|127.0.0.1:3901/ {print $1; exit}')
if garage layout show | grep -q "No nodes currently have a role"; then
  garage layout assign -z local -c 200G "$node_id"
  garage layout apply --version 1
fi

if ! grep -q '^ACCESS_KEY=' .env; then
  key_out=$(garage key create fluix-spike)
  key_id=$(echo "$key_out" | awk '/Key ID/ {print $NF}')
  secret=$(echo "$key_out" | awk '/Secret key/ {print $NF}')
  printf 'ACCESS_KEY=%s\nSECRET_KEY=%s\n' "$key_id" "$secret" >> .env
fi
# The bucket is checked on its own: a Garage that predates the rename has the key
# but not the bucket.
if ! garage bucket info fluix >/dev/null 2>&1; then
  garage bucket create fluix
  garage bucket allow --read --write --owner fluix --key "$(grep '^ACCESS_KEY=' .env | cut -d= -f2)"
fi

toxi delete s3 >/dev/null 2>&1 || true
toxi create -l 0.0.0.0:4501 -u garage:3900 s3
toxi toxic add -t latency -a latency="$LATENCY_MS" s3
echo "S3 direct: http://127.0.0.1:4500  simulated link: http://127.0.0.1:4501 (${LATENCY_MS} ms)"

toxi delete pg >/dev/null 2>&1 || true
toxi create -l 0.0.0.0:4504 -u postgres:5432 pg
toxi toxic add -t latency -a latency="$PG_LATENCY_MS" pg
echo "PostgreSQL through the simulated link: toxiproxy:4504 (${PG_LATENCY_MS} ms)"

docker compose up -d --build drive
echo "Mount from Finder: smb://drive@localhost:4503/Fluix (password: SMB_PASSWORD in spike/.env)"
