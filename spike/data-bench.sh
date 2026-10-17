#!/usr/bin/env bash
# Data-path benchmark: where cold reads lose bandwidth between S3, JuiceFS and
# the Mac's SMB client. Every measure starts cold (JuiceFS cache evicted, page
# cache dropped, SMB remounted). Needs setup.sh run; uploads three clips from
# footage/ on first use.
#
#   inside  sequential read inside the Linux box (JuiceFS alone)
#   seq     sequential read from the Mac over SMB, 1 MB reads
#   seek    20 random 1 MB reads from the Mac
#   scrub   10 jumps, each followed by 4 MB of playback in 256 KB reads
#
# Targets (ROADMAP 1): seq at least 80% of the line, seek mean under 0.4 s,
# no single read over 3 s. LABEL names the configuration in the output.
set -euo pipefail
cd "$(dirname "$0")"

MNT="${MNT:-$HOME/FloppySpike}"
LABEL="${LABEL:-baseline}"
SEQ_MB="${SEQ_MB:-200}"
LINE_MBPS="${LINE_MBPS:-${DOWNLOAD_LIMIT_MBPS:-100}}"
CDIR=/mnt/drive/data
drive() { docker compose exec -T drive "$@"; }

mount_share() {
  mkdir -p "$MNT"
  /sbin/mount_smbfs "//drive:$(grep '^SMB_PASSWORD=' .env | cut -d= -f2)@localhost:4503/Floppy" "$MNT"
}
# -f: a gentle unmount can leave a stale SMB session that answers I/O errors.
unmount_share() { /sbin/umount -f "$MNT" 2>/dev/null || true; }

ensure_clips() {
  if drive test -f "$CDIR/clip_3.mov"; then return; fi
  drive mkdir -p "$CDIR"
  for i in 1 2 3; do
    docker compose cp "footage/clip_$i.mov" "drive:/tmp/clip_$i.mov"
    drive sh -c "cp /tmp/clip_$i.mov $CDIR/ && rm /tmp/clip_$i.mov"
  done
}

cold() {
  unmount_share
  drive juicefs warmup --evict "$CDIR" >/dev/null 2>&1
  drive sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
  mount_share
}

# Mac-side reads with the page cache bypassed (F_NOCACHE), timing every read.
mac() {
  python3 - "$@" <<'PY'
import fcntl, os, random, sys, time
mode, path = sys.argv[1], sys.argv[2]
fd = os.open(path, os.O_RDONLY); fcntl.fcntl(fd, 48, 1)  # F_NOCACHE
size = os.fstat(fd).st_size; random.seed(7)
def timed(off, n):
    t = time.time(); os.pread(fd, n, off); return time.time() - t
if mode == "seq":
    total = int(sys.argv[3]) * 2**20; t0 = time.time()
    lat = [timed(off, 2**20) for off in range(0, total, 2**20)]
    el = time.time() - t0
    print(f"{total / 1e6 / el:.1f} MB/s, max read {max(lat):.2f} s")  # decimal MB, like the line
elif mode == "seek":
    lat = [timed(random.randrange(0, size - 2**20), 2**20) for _ in range(20)]
    print(f"mean {sum(lat) / len(lat):.2f} s, max {max(lat):.2f} s")
else:  # scrub
    first, jumps = [], []
    for _ in range(10):
        off = random.randrange(0, size - 4 * 2**20); t = time.time()
        reads = [timed(off + k * 2**18, 2**18) for k in range(16)]
        first.append(reads[0]); jumps.append(time.time() - t)
    print(f"first frame mean {sum(first) / 10:.2f} s, max {max(first):.2f} s; "
          f"4 MB after a jump mean {sum(jumps) / 10:.2f} s, max {max(jumps):.2f} s")
PY
}

ensure_clips
line=$(python3 -c "print(f'{$LINE_MBPS / 8:.1f}')")
echo "[$LABEL] line ${line} MB/s"

drive sh -c "juicefs warmup --evict $CDIR >/dev/null 2>&1; sync; echo 3 > /proc/sys/vm/drop_caches"
inside=$(drive sh -c "dd if=$CDIR/clip_1.mov of=/dev/null bs=1M count=$SEQ_MB 2>&1 | tail -1")
echo "[$LABEL] inside: ${inside##*, }"
cold; echo "[$LABEL] seq:    $(mac seq "$MNT/data/clip_1.mov" "$SEQ_MB")"
cold; echo "[$LABEL] seek:   $(mac seek "$MNT/data/clip_2.mov")"
cold; echo "[$LABEL] scrub:  $(mac scrub "$MNT/data/clip_3.mov")"
unmount_share
