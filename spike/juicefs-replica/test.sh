#!/usr/bin/env bash
# Tests the read-replica patch against a real Valkey master and replica in
# Docker: the Go integration tests in pkg/meta (TestRedisReplica*), then a
# mount sanity check (format, mount with read-replica, create, list, remove).
#
#   spike/juicefs-replica/test.sh            # v1.4.1 + 0001-*.patch, cloned once
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
JUICEFS_VERSION=v1.4.1
NET=floppy-jfs-replica
SUBNET="${SUBNET:-10.250.46.0/24}"
RUN="${RUN:-TestRedisReplica}"
work="${WORK:-${TMPDIR:-/tmp}/floppy-juicefs-replica}"
master=floppy-jfs-master
replica=floppy-jfs-replica

src="${JFS_SRC:-}"
if [ -z "$src" ]; then
  src="$work/src"
  if [ ! -d "$src/.git" ]; then
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$JUICEFS_VERSION" https://github.com/juicedata/juicefs.git "$src"
  fi
  git -C "$src" checkout --quiet --force "$JUICEFS_VERSION"
  git -C "$src" clean --quiet -fdx
  git -C "$src" apply "$here"/0001-*.patch
fi

cleanup() {
  docker rm -f "$master" "$replica" floppy-jfs-test floppy-jfs-mount >/dev/null 2>&1 || true
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
echo "all good"
