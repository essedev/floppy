#!/usr/bin/env bash
# The spike's measures on a real install: a team disk mounted by the app, its
# metadata on the meta service with a replica in the VM, its data on the real
# S3. Works in a folder of its own on the disk and removes it at the end.
#
#   line     the Mac's line (networkQuality) and the round trip to S3 and meta
#   upload   three clips copied from the Mac with cp, which returns once JuiceFS
#            has uploaded them (no writeback); during the third, the file is
#            stat'ed every 0.5 s: latency, and whether mtime moves while nothing
#            writes (JuiceMount saw macOS stall about 73 s on such a file)
#   cold     every read with JuiceFS's cache evicted and the VM's page cache
#            dropped: 1 MB reads with F_NOCACHE, 20 seeks, scrubbing, and a
#            whole clip with dd through the Mac's cache
#   meta     the workload of meta-bench.sh: 450 files seeded, listing, walk,
#            headers, create, rename and delete of 100 files
#   edit     an editing session as CapCut would run it, cold and through the
#            Mac's cache: import (each clip's header and its index at the
#            end), a filmstrip of 30 frames per clip, 20 s of playback at the
#            clip's bitrate, a cut to another clip, 5 jumps with 1 s of
#            playback each, and a 300 KB project autosave every 5 s meanwhile.
#            A model of the access pattern, not a trace of CapCut.
#
# Usage: spike/ovh-bench.sh [line upload cold meta edit]   (default: all)
# BENCH_DIR (default /Volumes/Floppy/<first disk>/_test), CLIPS (spike/footage).
set -euo pipefail
cd "$(dirname "$0")"

FLOPPY_HOME="${FLOPPY_HOME:-$HOME/.floppy}"
VM="${FLOPPY_VM:-floppy}"
CLIPS="${CLIPS:-$PWD/footage}"
SEQ_MB="${SEQ_MB:-200}"
OUT="${OUT:-ovh-bench.$(date +%Y%m%d-%H%M%S).tsv}"
FLOPPY="$FLOPPY_HOME/bin/floppy"

in_vm() { LIMA_HOME="$FLOPPY_HOME/vms" "$FLOPPY_HOME/lima/bin/limactl" shell --workdir / "$VM" "$@"; }
record() { printf '%s\t%s\n' "$1" "$2" | tee -a "$OUT"; }

mp=$(/sbin/mount | sed -En 's#^//drive@[^/]+/Floppy on (.*) \(smbfs.*#\1#p' | head -1)
[ -n "$mp" ] || { echo "ovh-bench: the disk is not mounted" >&2; exit 1; }
if [ -z "${BENCH_DIR:-}" ]; then
  first=$(find "$mp" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | head -1)
  [ -n "$first" ] || { echo "ovh-bench: no disk folder under $mp" >&2; exit 1; }
  BENCH_DIR="$first/_test"
fi
case "$BENCH_DIR" in "$mp"/*/*) ;; *) echo "ovh-bench: BENCH_DIR must be inside a disk folder" >&2; exit 1 ;; esac
# The folder is the bench's own: refuse one that already holds something else.
if [ -e "$BENCH_DIR" ] && [ ! -f "$BENCH_DIR/.ovh-bench" ]; then
  echo "ovh-bench: $BENCH_DIR exists and is not the bench's" >&2
  exit 1
fi
mkdir -p "$BENCH_DIR"
: > "$BENCH_DIR/.ovh-bench"

cold() {
  "$FLOPPY" free "$BENCH_DIR" >/dev/null
  in_vm sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
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
    print(f"{total / 1e6 / el:.1f} MB/s, max read {max(lat):.2f} s")
elif mode == "seek":
    lat = [timed(random.randrange(0, size - 2**20), 2**20) for _ in range(20)]
    print(f"mean {sum(lat) / len(lat):.2f} s, max {max(lat):.2f} s")
else:  # scrub
    first, jumps = [], []
    for _ in range(10):
        off = random.randrange(0, size - 4 * 2**20); t = time.time()
        reads = [timed(off + k * 2**18, 2**18) for k in range(16)]
        first.append(reads[0]); jumps.append(time.time() - t)
    print(f"first read mean {sum(first) / 10:.2f} s, max {max(first):.2f} s; "
          f"4 MB after a jump mean {sum(jumps) / 10:.2f} s, max {max(jumps):.2f} s")
PY
}

# stat every 0.5 s until the stop file appears: latency, mtime changes, and
# mtime changes while the size stood still (nothing was being written).
watch_stat() {
  python3 - "$1" "$2" <<'PY'
import os, sys, time
path, stop = sys.argv[1], sys.argv[2]
lat, moves, idle_moves, prev = [], 0, 0, None
while not os.path.exists(stop):
    t = time.time()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        time.sleep(0.5); continue
    lat.append(time.time() - t)
    if prev and st.st_mtime_ns != prev.st_mtime_ns:
        moves += 1
        if st.st_size == prev.st_size: idle_moves += 1
    prev = st
    time.sleep(0.5)
print(f"{len(lat)} stats, max {max(lat or [0]):.2f} s, mtime moved {moves} times, "
      f"{idle_moves} with the size unchanged")
PY
}

timed_cp() {  # <source> <destination>: MB/s
  python3 - "$1" "$2" <<'PY'
import os, subprocess, sys, time
src, dst = sys.argv[1], sys.argv[2]; t = time.time()
subprocess.run(["/bin/cp", src, dst], check=True); el = time.time() - t
print(f"{os.path.getsize(src) / 1e6 / el:.1f} MB/s ({el:.0f} s)")
PY
}

phase_line() {
  local nq
  nq=$(networkQuality -c 2>/dev/null || true)
  record line "$(printf '%s' "$nq" | python3 -c 'import json, sys
d = json.load(sys.stdin)
down, up, rtt = d["dl_throughput"] / 1e6, d["ul_throughput"] / 1e6, d.get("base_rtt", 0)
print(f"{down:.0f} Mbps down, {up:.0f} up, idle {rtt:.0f} ms")' 2>/dev/null || echo unknown)"
  local meta s3
  meta=$(python3 -c 'import json, sys
print(json.load(open(sys.argv[1]))["disks"][0]["meta_url"].split("//")[1].split(":")[0])' "$FLOPPY_HOME/disks.json")
  record rtt_meta "$(rtt "https://$meta")"
  # In manual mode the bucket lives in the format, not in the device's config.
  s3="${S3_URL:-$(in_vm sudo sh -c 'grep -h "^BUCKET_URL=" /etc/floppy/disks/*/mount.env | cut -d= -f2- | head -1' 2>/dev/null || true)}"
  [ -z "$s3" ] || record rtt_s3 "$(rtt "$(printf '%s' "$s3" | cut -d/ -f1-3)")"
}
rtt() {  # <url>: the median of 5 TCP connects
  local i
  for i in 1 2 3 4 5; do curl -so /dev/null --max-time 3 -w '%{time_connect}\n' "$1" || true; done \
    | sort -n | sed -n 3p | awk '{printf "%.0f ms TCP connect\n", $1 * 1000}'
}

phase_upload() {
  local i stop="$BENCH_DIR/.stop" watch
  rm -f "$BENCH_DIR"/clip_*.mov "$stop"
  for i in 1 2; do record "upload_clip_$i" "$(timed_cp "$CLIPS/clip_$i.mov" "$BENCH_DIR/")"; done
  watch=$(mktemp)
  watch_stat "$BENCH_DIR/clip_3.mov" "$stop" > "$watch" &
  record upload_clip_3 "$(timed_cp "$CLIPS/clip_3.mov" "$BENCH_DIR/")"
  sleep 10  # after the copy nothing writes: mtime must stand still
  : > "$stop"
  wait
  record stat_during_copy "$(cat "$watch")"
  rm -f "$watch" "$stop"
}

phase_cold() {
  [ -f "$BENCH_DIR/clip_3.mov" ] || phase_upload
  cold; record cold_seq "$(mac seq "$BENCH_DIR/clip_1.mov" "$SEQ_MB")"
  cold; record cold_seek "$(mac seek "$BENCH_DIR/clip_2.mov")"
  cold; record cold_scrub "$(mac scrub "$BENCH_DIR/clip_3.mov")"
  cold; record cold_dd_clip "$(dd if="$BENCH_DIR/clip_1.mov" of=/dev/null bs=1m 2>&1 | tail -1 | sed 's/.*(\(.*\))/\1/')"
  record warm_seek "$(mac seek "$BENCH_DIR/clip_1.mov")"
  cold; record warmup_clip "$(python3 -c 'import subprocess,sys,time
t = time.time(); subprocess.run([sys.argv[1], "warmup", sys.argv[2]], check=True, capture_output=True)
print(f"{time.time() - t:.0f} s")' "$FLOPPY" "$BENCH_DIR/clip_2.mov")"
}

phase_meta() {
  local tree="$BENCH_DIR/meta"
  rm -rf "$tree"
  python3 - "$tree" <<'PY' | while IFS=$'\t' read -r k v; do record "$k" "$v s"; done
import os, sys, time
root = sys.argv[1]
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
print(f"seed_450_files\t{timed(seed):.2f}", flush=True)
PY
  cold
  sleep 5  # the Mac's SMB client keeps attributes a few seconds
  python3 - "$tree" <<'PY' | while IFS=$'\t' read -r k v; do record "$k" "$v s"; done
import os, sys, time
root = sys.argv[1]
media = os.path.join(root, "media"); tmp = os.path.join(root, "autosave")
def timed(fn):
    t = time.perf_counter(); fn(); return time.perf_counter() - t
def listing():
    for e in os.scandir(media): e.stat()
def walk():
    for d, _, files in os.walk(root):
        for n in files: os.stat(os.path.join(d, n))
def headers():
    for i in range(100):
        with open(os.path.join(media, f"clip_{i:03d}.mov"), "rb") as f: f.read(64 * 1024)
def create():
    os.makedirs(tmp, exist_ok=True)
    for i in range(100):
        with open(os.path.join(tmp, f"a_{i:03d}.json"), "wb") as f: f.write(b"{}" * 2048)
def rename():
    for i in range(100): os.rename(os.path.join(tmp, f"a_{i:03d}.json"), os.path.join(tmp, f"b_{i:03d}.json"))
def delete():
    for i in range(100): os.remove(os.path.join(tmp, f"b_{i:03d}.json"))
    os.rmdir(tmp)
for name, fn in [("list_300_cold", listing), ("list_300_warm", listing), ("walk_450", walk),
                 ("headers_100", headers), ("create_100", create), ("rename_100", rename), ("delete_100", delete)]:
    print(f"{name}\t{timed(fn):.2f}", flush=True)
PY
}

# Also drops what the Mac's own cache holds of the clips: the share is
# unmounted (gently: a busy disk stops the bench) and mounted by floppy start.
cold_mac() {
  cold
  /usr/sbin/diskutil unmount "$mp" >/dev/null || { echo "ovh-bench: the disk is busy" >&2; exit 1; }
  "$FLOPPY" start >/dev/null
  [ -d "$BENCH_DIR" ] || { echo "ovh-bench: $BENCH_DIR is gone after the remount" >&2; exit 1; }
}

phase_edit() {
  [ -f "$BENCH_DIR/clip_3.mov" ] || phase_upload
  cold_mac
  python3 - "$BENCH_DIR" "${CLIP_MBPS:-61}" <<'PY' | while IFS=$'\t' read -r k v; do record "$k" "$v"; done
import os, sys, threading, time
root, mbps = sys.argv[1], float(sys.argv[2])
rate = mbps * 1e6 / 8  # bytes per second of playback
clips = [os.path.join(root, f"clip_{i}.mov") for i in (1, 2, 3)]
files = {}
def out(k, v): print(f"{k}\t{v}", flush=True)
def read(path, off, n):
    f = files.setdefault(path, open(path, "rb"))
    t = time.time(); f.seek(off); f.read(n); return time.time() - t
# Import: the header, then the index (moov), which a camera writes at the end.
t = time.time()
for c in clips:
    size = os.path.getsize(c); read(c, 0, 64 * 1024); read(c, max(0, size - 2**20), 2**20)
out("edit_import_3_clips", f"{time.time() - t:.2f} s")
# The filmstrip: one frame's worth every 1/30 of the clip.
lat = []
for c in clips:
    size = os.path.getsize(c)
    lat += [read(c, k * (size // 30), 256 * 1024) for k in range(30)]
out("edit_filmstrip_90_frames", f"{sum(lat):.1f} s, max frame {max(lat):.2f} s")
# Autosave beside the playback: write a temporary file, then rename it over.
saves, stop = [], threading.Event()
def autosave():
    proj = os.path.join(root, "project"); os.makedirs(proj, exist_ok=True)
    blob = b"{" + b"x" * (300 * 1024) + b"}"
    while not stop.wait(5):
        t = time.time(); tmp = os.path.join(proj, "draft_content.json.tmp")
        with open(tmp, "wb") as f: f.write(blob)
        os.replace(tmp, os.path.join(proj, "draft_content.json")); saves.append(time.time() - t)
saver = threading.Thread(target=autosave); saver.start()
# Playback paced at the bitrate in 256 KB reads: a read late by more than
# half a second against its deadline is a visible stall.
def play(path, start, seconds):
    chunk = 256 * 1024; t0 = time.time(); first = None; late = []
    for k in range(int(seconds * rate / chunk)):
        due = t0 + k * chunk / rate
        if time.time() < due: time.sleep(due - time.time())
        d = read(path, start + k * chunk, chunk)
        if first is None: first = d
        late.append(time.time() - due)
    return first, late
size = os.path.getsize(clips[0])
first, late = play(clips[0], int(size * 0.27), 20)
stalls = [x for x in late if x > 0.5]
out("edit_play_20s", f"start {first:.2f} s, {len(stalls)} stalls, worst late {max(late):.2f} s")
first, late = play(clips[1], int(os.path.getsize(clips[1]) * 0.63), 10)
out("edit_cut_play_10s", f"start {first:.2f} s, worst late {max(late):.2f} s")
starts = []
size = os.path.getsize(clips[2])
for k in range(5):
    # Off the filmstrip's frames, which are already in cache.
    starts.append(play(clips[2], size * (2 * k + 1) // 11, 1)[0])
out("edit_jumps_5", f"first frame mean {sum(starts) / 5:.2f} s, max {max(starts):.2f} s")
stop.set(); saver.join()
out("edit_autosave", f"{len(saves)} saves, max {max(saves or [0]):.2f} s")
PY
}

phases=("$@")
[ ${#phases[@]} -gt 0 ] || phases=(line upload cold meta edit)
printf 'measure\tvalue\n' > "$OUT"
echo "ovh-bench: in $BENCH_DIR, results in spike/$OUT" >&2
for p in "${phases[@]}"; do
  case "$p" in
    line|upload|cold|meta|edit) "phase_$p" ;;
    *) echo "ovh-bench: unknown phase $p" >&2; exit 2 ;;
  esac
done
# The bench's files leave the disk and the cache; the bucket keeps their old
# versions until its lifecycle expires them.
"$FLOPPY" free "$BENCH_DIR" >/dev/null 2>&1 || true
rm -rf "$BENCH_DIR"
