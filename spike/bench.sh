#!/usr/bin/env bash
# Spike benchmark: upload, cold read, warm read, random seeks, warmup.
# Needs setup.sh run and the share mounted at $MNT (smb://drive@localhost:4503/FloppySpike).
set -euo pipefail
cd "$(dirname "$0")"

# Mount point inside the repo (spike/.local, outside git)
MNT="${MNT:-$PWD/.local/mnt/drive}"
DIR="$MNT/bench"
CDIR=/mnt/drive/bench
drive() { docker compose exec -T drive "$@"; }
now() { python3 -c 'import time; print(time.time())'; }
secs() { python3 -c "print(f'{$2 - $1:.1f}')"; }
remount() {
  # Right after heavy I/O the share can be briefly busy.
  for _ in 1 2 3 4 5; do /sbin/umount "$MNT" 2>/dev/null && break; sleep 2; done
  /sbin/mount_smbfs "//drive:$(grep '^SMB_PASSWORD=' .env | cut -d= -f2)@localhost:4503/FloppySpike" "$MNT"
}
evict() { drive juicefs warmup --evict "$CDIR" >/dev/null 2>&1; remount; }
read_file() { dd if="$1" of=/dev/null bs=4m 2>/dev/null; }
# 20 random 1 MB reads; prints the average and max latency in ms.
seeks() {
  python3 - "$1" <<'PY'
import os, random, sys, time
p = sys.argv[1]; size = os.path.getsize(p); random.seed(7); lat = []
with open(p, 'rb', buffering=0) as f:
    for _ in range(20):
        f.seek(random.randrange(0, size - 2**20)); t = time.time(); f.read(2**20)
        lat.append((time.time() - t) * 1000)
print(f"avg {sum(lat)/len(lat):.0f} ms, max {max(lat):.0f} ms")
PY
}

if [ -z "${SKIP_UPLOAD:-}" ]; then
  rm -rf "$DIR"; mkdir -p "$DIR"
  size_mb=$(du -cm footage/clip_*.mov | tail -1 | cut -f1)
  t=$(now); cp footage/clip_*.mov "$DIR/"; e=$(now)
  echo "upload ${size_mb} MB: $(secs "$t" "$e") s"
fi

evict
t=$(now); read_file "$DIR/clip_1.mov"; e=$(now)
echo "cold read 434 MB: $(secs "$t" "$e") s"
remount
t=$(now); read_file "$DIR/clip_1.mov"; e=$(now)
echo "warm read 434 MB: $(secs "$t" "$e") s"

echo "cold seeks clip_2: $(seeks "$DIR/clip_2.mov")"
echo "warm seeks clip_2: $(seeks "$DIR/clip_2.mov")"

t=$(now); drive juicefs warmup "$CDIR/clip_3.mov" >/dev/null 2>&1; e=$(now)
echo "warmup 434 MB: $(secs "$t" "$e") s"
remount
echo "seeks after warmup clip_3: $(seeks "$DIR/clip_3.mov")"
