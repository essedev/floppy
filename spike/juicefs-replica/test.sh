#!/usr/bin/env bash
# Tests the patches: the Go integration tests of the read replica in pkg/meta
# (TestRedisReplica*) against a real Valkey master and replica in Docker, the
# pkg/vfs tests with the whole-block reads (0002), the storage keys from the
# environment in cmd (0003), then a mount sanity check (format, mount with
# read-replica, create, list, remove), then storage keys on Garage: a volume with
# no stored key where each mount brings its own and loses access alone when it is
# deleted, and a stored team key rotated by `juicefs config` from the environment
# (0003).
#
#   spike/juicefs-replica/test.sh            # the Dockerfile's upstream tag + the patches, cloned once
#   JFS_SRC=/path/to/juicefs spike/juicefs-replica/test.sh   # an existing tree
#   RUN='TestRedisReplicaMasterDown' spike/juicefs-replica/test.sh   # RUN=none: mount only
#   VALKEY_IMAGE=valkey/valkey:8.1.1 spike/juicefs-replica/test.sh  # Debian 13 ships 8.1.1
#
# Containers, the network (explicit subnet, SUBNET to change it) and the
# random Valkey password exist only while the script runs. The Go caches are
# the Docker volumes floppy-jfs-gomod and floppy-jfs-gobuild.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)

VALKEY_IMAGE="${VALKEY_IMAGE:-valkey/valkey:8.1.4}"
GO_IMAGE=golang:1.25.14-bookworm
JUICEFS_VERSION=$(sed -n 's/^ARG JUICEFS_VERSION=//p' "$here/Dockerfile") # the tag build.sh builds
NET=floppy-jfs-replica
SUBNET="${SUBNET:-10.250.46.0/24}"
RUN="${RUN:-TestRedisReplica}"
work="${WORK:-${TMPDIR:-/tmp}/floppy-juicefs-replica}"
master=floppy-jfs-master
replica=floppy-jfs-replica
plain=floppy-jfs-plain # pkg/vfs's tests expect a Redis without a password on 127.0.0.1

src="${JFS_SRC:-}"
if [ -z "$src" ]; then
  src="$work/src"
  if [ ! -d "$src/.git" ]; then
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$JUICEFS_VERSION" https://github.com/juicedata/juicefs.git "$src"
  fi
  git -C "$src" checkout --quiet --force "$JUICEFS_VERSION"
  git -C "$src" clean --quiet -fdx
  for p in "$here"/*.patch; do git -C "$src" apply "$p"; done
fi

cleanup() {
  docker rm -f "$master" "$replica" "$plain" floppy-jfs-test floppy-jfs-mount floppy-jfs-garage >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
docker network create --subnet "$SUBNET" "$NET" >/dev/null

# The password reaches the containers through the environment, never argv here.
META_PASSWORD=$(openssl rand -hex 16)
export META_PASSWORD REDISCLI_AUTH="$META_PASSWORD"
valkey() {
  local name=$1; shift
  docker run -d --name "$name" --hostname "$name" --network "$NET" -e META_PASSWORD "$VALKEY_IMAGE" \
    sh -c 'exec valkey-server --requirepass "$META_PASSWORD" --masterauth "$META_PASSWORD" --save "" --appendonly no "$@"' \
    sh "$@" >/dev/null
}
valkey "$master"
valkey "$replica" --replicaof "$master" 6379 --replica-read-only yes
for _ in $(seq 100); do
  if docker exec -e REDISCLI_AUTH "$replica" valkey-cli info replication 2>/dev/null \
      | grep -q 'master_link_status:up'; then
    break
  fi
  sleep 0.1
done

go_run() {
  docker run --rm --network "$NET" -e META_PASSWORD \
    -v "$src:/src" -v floppy-jfs-gomod:/go/pkg/mod -v floppy-jfs-gobuild:/root/.cache/go-build \
    -w /src "$@"
}

if [ "$RUN" != none ]; then
  echo "== go test -run $RUN ./pkg/meta/ (full log: $work/go-test.log)"
  mkdir -p "$work"
  status=0
  go_run --name floppy-jfs-test --hostname floppy-jfs-test \
    -e JFS_TEST_REPLICA_MASTER="$master:6379" -e JFS_TEST_REPLICA="$replica:6379" \
    -e JFS_TEST_REPLICA_SELF=floppy-jfs-test \
    "$GO_IMAGE" go test -count=1 -timeout 60m -v -run "$RUN" ./pkg/meta/ > "$work/go-test.log" 2>&1 || status=$?
  grep -E '^(=== RUN|\s*--- |PASS|FAIL|ok|panic)|redis_replica_test.go:' "$work/go-test.log" || true
  if [ "$status" -ne 0 ]; then
    echo "go test failed ($status)" >&2
    exit 1
  fi
fi

if [ "$RUN" != none ]; then
  echo "== go test ./pkg/vfs/ (full log: $work/go-test-vfs.log)"
  docker run -d --name "$plain" --network "$NET" "$VALKEY_IMAGE" >/dev/null
  status=0
  docker run --rm --network "container:$plain" \
    -v "$src:/src" -v floppy-jfs-gomod:/go/pkg/mod -v floppy-jfs-gobuild:/root/.cache/go-build -w /src \
    "$GO_IMAGE" go test -count=1 ./pkg/vfs/ > "$work/go-test-vfs.log" 2>&1 || status=$?
  grep -E '^(--- |FAIL|ok|panic)' "$work/go-test-vfs.log" || true
  if [ "$status" -ne 0 ]; then
    echo "go test ./pkg/vfs/ failed ($status)" >&2
    exit 1
  fi
fi

if [ "$RUN" != none ]; then
  echo "== go test ./cmd/ (storage keys from the environment, full log: $work/go-test-cmd.log)"
  status=0
  go_run "$GO_IMAGE" go test -count=1 \
    -run 'TestKeysFromEnv|TestKeysOnTheCommandLine|TestNoKeysInTheEnvironment' \
    ./cmd/ > "$work/go-test-cmd.log" 2>&1 || status=$?
  grep -E '^(--- |FAIL|ok|panic)' "$work/go-test-cmd.log" || true
  if [ "$status" -ne 0 ]; then
    echo "go test ./cmd/ failed ($status)" >&2
    exit 1
  fi
fi

echo "== mount sanity"
go_run "$GO_IMAGE" go build -o /src/juicefs.replica . >/dev/null
docker run -d --name floppy-jfs-mount --network "$NET" -e META_PASSWORD \
  --device /dev/fuse --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
  -v "$src/juicefs.replica:/usr/local/bin/juicefs:ro" "$GO_IMAGE" sleep infinity >/dev/null
in_mount() { docker exec floppy-jfs-mount bash -euo pipefail -c "$1"; }
replica_reads() {
  docker exec -e REDISCLI_AUTH "$replica" valkey-cli info commandstats \
    | awk -F'[=:,]' '/^cmdstat_(get|mget|hget|hkeys|hscan|lrange):/ {n += $3} END {print n + 0}'
}
url="redis://$master:6379/5?read-replica=redis://$replica:6379&max-lag=2s"
in_mount "apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends fuse3 >/dev/null 2>&1
  juicefs version
  juicefs format --storage file --bucket /tmp/jfs-data/ '$url' sanity >/dev/null 2>&1
  juicefs mount -d --log /tmp/jfs.log '$url' /mnt/jfs >/dev/null 2>&1
  for _ in \$(seq 50); do grep -q '^juicefs_meta_replica_mode 1' /mnt/jfs/.stats && exit 0; sleep 0.1; done
  echo 'not in replica mode' >&2; exit 1"
before=$(replica_reads)
# shellcheck disable=SC2016 # expanded inside the container
in_mount 'cd /mnt/jfs
  mkdir -p a/b
  for i in $(seq 200); do echo "file $i" > "a/b/f$i"; done
  test "$(ls a/b | wc -l)" -eq 200
  test "$(cat a/b/f123)" = "file 123"
  ls -l a/b > /dev/null
  mv a/b/f1 a/f1 && test -f a/f1 && test ! -e a/b/f1
  rm -r a
  test -z "$(ls /mnt/jfs)"
  echo "create/list/stat/rename/remove OK"'
echo "read commands served by the replica during the test: $(( $(replica_reads) - before ))"
in_mount 'grep "^juicefs_meta_replica" /mnt/jfs/.stats
  umount /mnt/jfs
  grep -o "Read replica.*" /tmp/jfs.log'
echo "== storage keys on Garage"
GARAGE_RPC_SECRET=$(openssl rand -hex 32)
export GARAGE_RPC_SECRET
docker run -d --name floppy-jfs-garage --network "$NET" -e GARAGE_RPC_SECRET \
  -v "$here/../garage.toml:/etc/garage.toml:ro" dxflrs/garage:v2.1.0 >/dev/null
garage() { docker exec floppy-jfs-garage /garage "$@" 2>/dev/null; }
for _ in $(seq 50); do garage status >/dev/null 2>&1 && break; sleep 0.2; done
garage layout assign -z local -c 1G "$(garage status | awk '/NO ROLE ASSIGNED/ {print $1; exit}')" >/dev/null
garage layout apply --version 1 >/dev/null
garage bucket create keys >/dev/null
new_key() {  # new_key NAME: prints "<id> <secret>"; both stay in this script's variables
  garage key create "$1" | awk '/Key ID:/ {id=$3} /Secret key:/ {s=$3} END {print id, s}'
}
# in_keys VARS SCRIPT: runs SCRIPT in the mount container with the variables named in VARS
# passed through by name: values never reach an argument.
in_keys() {
  local flags=() name
  for name in $1; do flags+=(-e "$name"); done
  docker exec ${flags[@]+"${flags[@]}"} floppy-jfs-mount bash -euo pipefail -c "$2"
}
stored_access_key() {  # stored_access_key META_URL
  in_keys "" "juicefs config '$1' 2>/dev/null" | grep -o '"AccessKey": *"[^"]*"' | cut -d'"' -f4
}
bucket="http://floppy-jfs-garage:3900/keys"
export AWS_REGION=us-east-1
mount_at() {  # mount_at META_URL: the mount command, cold reads (no disk cache), few retries
  echo "mkdir -p /mnt/keys && juicefs mount -d --log /tmp/jfs-keys.log --cache-size 0 --io-retries 1 '$1' /mnt/keys >/dev/null 2>&1"
}

echo "-- a volume with no stored key: each mount brings its own (AWS_* variables)"
read -r a_id a_secret <<<"$(new_key mac-a)"
read -r b_id b_secret <<<"$(new_key mac-b)"
for k in "$a_id" "$b_id"; do garage bucket allow --read --write keys --key "$k" >/dev/null; done
url="redis://$master:6379/6"
AWS_ACCESS_KEY_ID=$a_id AWS_SECRET_ACCESS_KEY=$a_secret in_keys "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION" \
  "juicefs format --storage s3 --bucket $bucket/person '$url' person >/dev/null 2>&1"
test -z "$(stored_access_key "$url")"
# shellcheck disable=SC2016 # expanded inside the container
AWS_ACCESS_KEY_ID=$a_id AWS_SECRET_ACCESS_KEY=$a_secret in_keys "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION" \
  "$(mount_at "$url")"'
  head -c 3000000 /dev/urandom > /tmp/blob-a && cp /tmp/blob-a /mnt/keys/a && sync && umount /mnt/keys
  echo "key A: wrote"'
# shellcheck disable=SC2016 # expanded inside the container
AWS_ACCESS_KEY_ID=$b_id AWS_SECRET_ACCESS_KEY=$b_secret in_keys "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION" \
  "$(mount_at "$url")"'
  cmp /tmp/blob-a /mnt/keys/a
  head -c 3000000 /dev/urandom > /tmp/blob-b && cp /tmp/blob-b /mnt/keys/b && sync && umount /mnt/keys
  echo "key B: read what A wrote, wrote its own"'
garage key delete --yes "$a_id" >/dev/null
# shellcheck disable=SC2016 # expanded inside the container
if AWS_ACCESS_KEY_ID=$a_id AWS_SECRET_ACCESS_KEY=$a_secret in_keys "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION" \
    "$(mount_at "$url")"'
    trap "umount /mnt/keys" EXIT
    timeout 60 cat /mnt/keys/b > /dev/null' 2>/dev/null; then
  echo "key A was deleted at the provider and still read the data" >&2
  exit 1
fi
echo "key A, deleted at the provider: refused"
# shellcheck disable=SC2016 # expanded inside the container
AWS_ACCESS_KEY_ID=$b_id AWS_SECRET_ACCESS_KEY=$b_secret in_keys "AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION" \
  "$(mount_at "$url")"'
  cmp /tmp/blob-a /mnt/keys/a && cmp /tmp/blob-b /mnt/keys/b
  echo again > /mnt/keys/c && sync && umount /mnt/keys
  echo "key B: still reads and writes"'

echo "-- a team key stored in the volume, rotated by config from the environment (0003)"
read -r t1_id t1_secret <<<"$(new_key team-1)"
read -r t2_id t2_secret <<<"$(new_key team-2)"
for k in "$t1_id" "$t2_id"; do garage bucket allow --read --write keys --key "$k" >/dev/null; done
url="redis://$master:6379/7"
ACCESS_KEY=$t1_id SECRET_KEY=$t1_secret in_keys "ACCESS_KEY SECRET_KEY AWS_REGION" \
  "juicefs format --storage s3 --bucket $bucket/team '$url' team >/dev/null 2>&1"
test "$(stored_access_key "$url")" = "$t1_id"
# shellcheck disable=SC2016 # expanded inside the container
in_keys "" "$(mount_at "$url")"'
  echo team > /mnt/keys/t && sync && umount /mnt/keys'
ACCESS_KEY=$t2_id SECRET_KEY=$t2_secret in_keys "ACCESS_KEY SECRET_KEY AWS_REGION" \
  "juicefs config '$url' --yes >/dev/null 2>&1"
test "$(stored_access_key "$url")" = "$t2_id"
garage key delete --yes "$t1_id" >/dev/null
# shellcheck disable=SC2016 # expanded inside the container
in_keys "" "$(mount_at "$url")"'
  test "$(cat /mnt/keys/t)" = team && echo more > /mnt/keys/u && sync && umount /mnt/keys
  echo "team key rotated from the environment; the old one deleted; mounts use the new one"'
# A key the bucket refuses fails config's sanity check and leaves the format as it was.
read -r bad_id bad_secret <<<"$(new_key no-access)"
if ACCESS_KEY=$bad_id SECRET_KEY=$bad_secret in_keys "ACCESS_KEY SECRET_KEY AWS_REGION" \
    "juicefs config '$url' --yes >/dev/null 2>&1"; then
  echo "config accepted a key the bucket refuses" >&2
  exit 1
fi
test "$(stored_access_key "$url")" = "$t2_id"
echo "a refused key: config fails, the format keeps the working key"
echo "all good"
