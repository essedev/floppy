#!/bin/bash
# Tests of macos/bin/floppy that need no VM: a fake limactl logs every call and
# answers from files. Covers the engine lock (two contenders, a holder killed
# with SIGKILL, tick skipping a busy round) and `floppy disks clear` with the
# VM running, stopped, broken and missing. Everything happens in a temporary
# FLOPPY_HOME; it refuses to run while a Floppy share is mounted, since `disks
# clear` unmounts it. A fake df gives the Mac's free space for the cache's
# limits and the growth of the VM's disk.
#
#   macos/tests/engine.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
FLOPPY="$here/../bin/floppy"
if /sbin/mount | grep -q '^//drive@[^/]*/Floppy on '; then
  echo "engine.sh: a Floppy share is mounted; unmount it before running the tests" >&2
  exit 2
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export FLOPPY_HOME="$tmp/home" FLOPPY_VM=floppy-test FAKE="$tmp/fake"
mkdir -p "$FLOPPY_HOME/lima/bin" "$FAKE" "$tmp/bin"
cat > "$tmp/bin/df" <<'EOF'
#!/bin/bash
# The fake df: the Mac's free space in MB from $FAKE/free, or the real one.
[ -f "$FAKE/free" ] || exec /bin/df "$@"
printf 'Filesystem 1M-blocks Used Available Capacity Mounted\n/dev/disk3s5 500000 1 %s 1%% /\n' "$(cat "$FAKE/free")"
EOF
chmod +x "$tmp/bin/df"
export PATH="$tmp/bin:$PATH"
cat > "$FLOPPY_HOME/lima/bin/limactl" <<'EOF'
#!/bin/bash
# The fake limactl: one line per call in $FAKE/calls, prefixed by $CONTENDER.
log() { printf '%s %s\n' "${CONTENDER:--}" "$*" >> "$FAKE/calls"; }
case "$1" in
  list)
    case "$*" in
      *'{{.Disk}}'*) cat "$FAKE/disk" 2>/dev/null || true ;;
      *'{{.Memory}}'*) cat "$FAKE/memory" 2>/dev/null || true ;;
      *) log list begin; sleep "${FAKE_SLEEP:-0}"; cat "$FAKE/status" 2>/dev/null || true; log list end ;;
    esac ;;
  start) log start; echo Running > "$FAKE/status" ;;
  stop) log stop; echo Stopped > "$FAKE/status" ;;
  shell) shift 4; log shell "$*"; [ -f "$FAKE/broken" ] && exit 1; exit 0 ;;
  *) log "$*" ;;
esac
EOF
chmod +x "$FLOPPY_HOME/lima/bin/limactl"

failed=0
check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2  ($3)"; failed=1; fi; }
reset() { : > "$FAKE/calls"; rm -f "$FAKE/broken"; printf '%s\n' "$1" > "$FAKE/status"; }
# The contenders' order in the call log, with repeats collapsed: "ab" when a
# ran entirely before b, "aba" when they interleaved.
order() { grep -v '^- ' "$FAKE/calls" | cut -d' ' -f1 | uniq | tr -d '\n'; }
serial() { [ "$1" = ab ] || [ "$1" = ba ]; }

# Two contenders at once: one waits for the other.
reset Running
CONTENDER=a FAKE_SLEEP=1 "$FLOPPY" start 2>/dev/null &
CONTENDER=b FAKE_SLEEP=1 "$FLOPPY" start 2>/dev/null &
wait
o=$(order); if serial "$o"; then rc=0; else rc=1; fi
check "$rc" "two contenders run one after the other" "$o"

# A holder killed with SIGKILL (the app kills the engine's process group):
# the kernel drops its lock, the next two contenders get it at once and still
# do not overlap.
reset Running
set -m
CONTENDER=h FAKE_SLEEP=30 "$FLOPPY" start 2>/dev/null &
holder=$!
set +m
sleep 1
kill -9 -- "-$holder" 2>/dev/null
wait "$holder" 2>/dev/null
: > "$FAKE/calls"
t=$(date +%s)
CONTENDER=a FAKE_SLEEP=1 "$FLOPPY" start 2>/dev/null &
CONTENDER=b FAKE_SLEEP=1 "$FLOPPY" start 2>/dev/null &
wait
s=$(( $(date +%s) - t )); o=$(order)
if serial "$o" && [ "$s" -le 5 ]; then rc=0; else rc=1; fi
check "$rc" "after a killed holder: no stale lock, contenders in ${s} s, not overlapping" "$o"

# tick skips its round while another command holds the lock.
reset Running
CONTENDER=h FAKE_SLEEP=3 "$FLOPPY" start 2>/dev/null &
sleep 1
t=$(date +%s)
CONTENDER=t "$FLOPPY" tick 2>/dev/null; rc=$?
s=$(( $(date +%s) - t ))
wait
! grep -q '^t ' "$FAKE/calls" && [ "$rc" = 0 ] && [ "$s" -le 1 ]; check $? "tick skips a busy round at once" "rc $rc, ${s} s"

# disks clear.
disks='{"version":1,"meta_user":"u","disks":[]}'
clear_case() { # status, broken?, label, expected rc, disks file kept?, calls wanted, calls refused
  reset "$1"
  [ "$2" = broken ] && touch "$FAKE/broken"
  printf '%s\n' "$disks" > "$FLOPPY_HOME/disks.json"
  out=$("$FLOPPY" disks clear 2>/dev/null); rc=$?
  calls=$(cut -d' ' -f2- "$FAKE/calls" | grep -v '^list' | tr '\n' ';')
  local ok=0
  [ "$rc" = "$4" ] || ok=1
  if [ "$5" = kept ]; then [ -f "$FLOPPY_HOME/disks.json" ] || ok=1; else [ ! -f "$FLOPPY_HOME/disks.json" ] || ok=1; fi
  [ -z "$6" ] || printf '%s' "$calls" | grep -q "$6" || ok=1
  [ -z "$7" ] || ! printf '%s' "$calls" | grep -q "$7" || ok=1
  check $ok "disks clear, $3" "rc $rc, out ${out:-none}, calls $calls"
}
clear_case Running ok "VM running: cleaned in the VM" 0 gone 'shell sudo /usr/local/sbin/floppy-disks clear' 'start'
clear_case Stopped ok "VM stopped: started, cleaned, stopped again" 0 gone 'start;shell true;shell sudo /usr/local/sbin/floppy-disks clear;stop;' ''
clear_case Stopped broken "VM broken: fails and keeps the disks file" 1 kept 'start' 'floppy-disks clear'
clear_case "" ok "no VM: nothing to clean" 0 gone '' 'shell'

# The space for copies. A Mac with 45.8 GB free and the 20 GB reserve spares
# 25.3 GB: Floppy picks half, rounded down to 5 GB, and the person may choose
# up to 25 GB; the VM's disk of 22 GiB holds 12 GB of copies.
rm -f "$FAKE/free"
gb() { echo $(( $1 * 1073741824 )); }
team_disks() { # cache_mb or ""
  local chosen=""; [ -n "$1" ] && chosen="\"cache_mb\":$1,"
  printf '{"version":1,"meta_user":"u",%s"disks":[{"id":"a","folder":"Progetti","enabled":true}]}\n' "$chosen" > "$FLOPPY_HOME/disks.json"
  echo 46899 > "$FAKE/free"; gb 22 > "$FAKE/disk"; echo 1073741824 > "$FAKE/memory"
}
space_case() { # cache_mb or "", label, expected fields
  reset Running
  team_disks "$1"
  out=$("$FLOPPY" status --json 2>/dev/null)
  local ok=0 f
  for f in $3; do printf '%s' "$out" | grep -q "\"${f%%=*}\":${f#*=}[,}]" || ok=1; done
  check $ok "space for copies, $2" "$out"
}
space_case "" "Floppy's pick: 10 GB of 25 GB possible" "cache_chosen_mb=null cache_auto_mb=10240 cache_choice_max_mb=25600 cache_target_mb=10240 vm_disk_mb=22528 vm_disk_wanted_mb=22528"
space_case 30720 "a choice above what the Mac spares: cut to the VM's disk, which wants to grow" "cache_chosen_mb=30720 cache_target_mb=12288 vm_disk_wanted_mb=40960"
space_case 5120 "a choice below the pick: taken as is" "cache_chosen_mb=5120 cache_target_mb=5120 vm_disk_wanted_mb=22528"

# A start with the VM stopped grows its disk for the choice, never for Floppy's pick.
start_case() { # cache_mb or "", label, grows?
  reset Stopped
  team_disks "$1"
  "$FLOPPY" start >/dev/null 2>&1
  local ok=0
  if [ "$3" = grows ]; then grep -q -- 'edit --tty=false --disk=40 floppy-test' "$FAKE/calls" || ok=1
  else ! grep -q -- '--disk' "$FAKE/calls" || ok=1; fi
  check $ok "start, $2" "$(cut -d' ' -f2- "$FAKE/calls" | grep -v '^list' | tr '\n' ';')"
}
start_case 30720 "a choice of 30 GB grows the VM's disk to 40 GiB" grows
start_case "" "Floppy's pick leaves the disk as it is" same
rm -f "$FAKE/free" "$FLOPPY_HOME/disks.json"

exit $failed
