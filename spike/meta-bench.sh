#!/usr/bin/env bash
# Step 0 of the team architecture: what the metadata round trip costs the Finder
# and CapCut. Same file tree and the same operations, measured from the Mac over
# SMB, with the metadata in SQLite next to JuiceFS (today) and in PostgreSQL
# behind a simulated link (the shared disk). Needs setup.sh run first.
#
# Usage: spike/meta-bench.sh [scenario...]
#   scenarios: sqlite pg0 pg15 pg30 pg15-cache (default: all)
set -euo pipefail
cd "$(dirname "$0")"

# Mount point inside the repo (spike/.local, outside git)
MNT="${MNT:-$PWD/.local/mnt/drive}"
OUT="${OUT:-meta-bench.$(date +%Y%m%d-%H%M%S).tsv}"
CACHE_OPTS="--attr-cache 10 --entry-cache 10 --dir-entry-cache 10"
env_val() { grep "^$1=" .env | cut -d= -f2; }
PG_PASSWORD=$(env_val PG_PASSWORD)
SMB_PASSWORD=$(env_val SMB_PASSWORD)
export ACCESS_KEY SECRET_KEY PG_PASSWORD SMB_PASSWORD
ACCESS_KEY=$(env_val ACCESS_KEY)
SECRET_KEY=$(env_val SECRET_KEY)

psql_admin() { docker compose exec -T postgres psql -q -U floppy_admin -d floppy -v ON_ERROR_STOP=1 "$@"; }
toxi() { docker compose exec -T toxiproxy /toxiproxy-cli "$@"; }

unmount() {
  for _ in 1 2 3 4 5; do
    /sbin/mount | grep -q " on $MNT " || return 0
    /sbin/umount "$MNT" 2>/dev/null && return 0
    sleep 2
  done
  echo "meta-bench: cannot unmount $MNT" >&2
  return 1
}

mount_share() {
  mkdir -p "$MNT"
  for _ in $(seq 1 30); do
    /sbin/mount_smbfs "//drive:$SMB_PASSWORD@localhost:4503/FloppySpike" "$MNT" 2>/dev/null && return 0
    sleep 2
  done
  echo "meta-bench: the share did not come up" >&2
  docker compose logs --tail 30 drive >&2
  return 1
}

# Recreates the drive container on a fresh volume; JuiceFS starts with empty caches.
start_drive() {
  unmount
  META_URL="$1" VOLUME_NAME="$2" MOUNT_OPTS="${3:-}" docker compose up -d --force-recreate drive >/dev/null
  mount_share
}

restart_drive() {
  unmount
  docker compose restart drive >/dev/null
  mount_share
}

fresh_meta() {
  case "$1" in
    sqlite) rm -f .local/state-a/meta-bench.db; echo "sqlite3:///state/meta-bench.db" ;;
    *)
      psql_admin -c "DROP DATABASE IF EXISTS bench_$1" -c "CREATE DATABASE bench_$1" >/dev/null
      if [ "$2" = direct ]; then
        echo "postgres://floppy_admin:$PG_PASSWORD@postgres:5432/bench_$1?sslmode=disable"
      else
        echo "postgres://floppy_admin:$PG_PASSWORD@toxiproxy:4504/bench_$1?sslmode=disable"
      fi
      ;;
  esac
}

pg_latency() { toxi toxic update -n latency_downstream -a latency="$1" pg >/dev/null; }

# The workload, run on the Mac against the SMB mount. A project folder like the
# ones in the pilot: 300 media files and 150 small cache files in three folders.
workload() {
  python3 - "$MNT/bench" "$1" <<'PY'
import os, sys, time
root, phase = sys.argv[1], sys.argv[2]
media, cache = os.path.join(root, "media"), os.path.join(root, "cache")
def timed(fn):
    t = time.perf_counter(); fn(); return time.perf_counter() - t
def seed():
    os.makedirs(media, exist_ok=True)
    blob = os.urandom(64 * 1024)
    for i in range(300):
        with open(os.path.join(media, f"clip_{i:03d}.mov"), "wb") as f: f.write(blob)
    for d in range(3):
        p = os.path.join(cache, f"part{d}"); os.makedirs(p, exist_ok=True)
        for i in range(50):
            with open(os.path.join(p, f"thumb_{i:03d}.jpg"), "wb") as f: f.write(blob[:4096])
def listing():
    for e in os.scandir(media): e.stat()
def walk():
    for d, _, files in os.walk(root):
        for n in files: os.stat(os.path.join(d, n))
def headers():
    for i in range(100):
        with open(os.path.join(media, f"clip_{i:03d}.mov"), "rb") as f: f.read(64 * 1024)
tmp = os.path.join(root, "autosave")
def create():
    os.makedirs(tmp, exist_ok=True)
    for i in range(100):
        with open(os.path.join(tmp, f"a_{i:03d}.json"), "wb") as f: f.write(b"{}" * 2048)
def rename():
    for i in range(100): os.rename(os.path.join(tmp, f"a_{i:03d}.json"), os.path.join(tmp, f"b_{i:03d}.json"))
def delete():
    for i in range(100): os.remove(os.path.join(tmp, f"b_{i:03d}.json"))
    os.rmdir(tmp)
if phase == "seed":
    print(f"seed_450_files\t{timed(seed):.2f}")
else:
    for name, fn in [("list_300_cold", listing), ("list_300_warm", listing), ("walk_450", walk),
                     ("headers_100", headers), ("create_100", create), ("rename_100", rename), ("delete_100", delete)]:
        print(f"{name}\t{timed(fn):.2f}")
PY
}

run() {
  local name="$1" meta="$2" opts="${3:-}"
  echo "== $name" >&2
  start_drive "$meta" "bench-$name" "$opts"
  workload seed | sed "s/^/$name\t/" | tee -a "$OUT"
  restart_drive
  sleep 2
  workload measure | sed "s/^/$name\t/" | tee -a "$OUT"
}

scenarios=("$@")
[ ${#scenarios[@]} -gt 0 ] || scenarios=(sqlite pg0 pg15 pg30 pg15-cache)
printf 'scenario\toperation\tseconds\n' > "$OUT"
for s in "${scenarios[@]}"; do
  case "$s" in
    sqlite) run sqlite "$(fresh_meta sqlite)" ;;
    pg0) run pg0 "$(fresh_meta pg0 direct)" ;;
    pg15) pg_latency 15; run pg15 "$(fresh_meta pg15 proxy)" ;;
    pg30) pg_latency 30; run pg30 "$(fresh_meta pg30 proxy)" ;;
    pg15-cache) pg_latency 15; run pg15-cache "$(fresh_meta pg15cache proxy)" "$CACHE_OPTS" ;;
    *) echo "meta-bench: unknown scenario $s" >&2; exit 2 ;;
  esac
done
unmount
rmdir "$MNT" 2>/dev/null || true
echo "results in spike/$OUT" >&2
