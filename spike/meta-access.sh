#!/usr/bin/env bash
# Step 0 of the team architecture: the access model on the shared metadata.
#   1. A Mac mounts with a PostgreSQL role limited to reading and writing the
#      JuiceFS tables (no ownership, no DDL).
#   2. That role can read the S3 key from the volume format.
#   3. A new S3 key applied with `juicefs config` reaches the mounted client
#      after the old key stops working, and how long it takes.
#   4. Dropping the role stops the client, and the volume stays readable for
#      the admin.
# Needs setup.sh run first and nothing else using the spike (it denies the
# spike key on the bucket for a while, then restores it).
set -euo pipefail
cd "$(dirname "$0")"

# Mount point inside the repo (spike/.local, outside git)
MNT="${MNT:-$PWD/.local/mnt/drive}"
DB=access
ROLE=floppy_d_test
env_val() { grep "^$1=" .env | cut -d= -f2; }
PG_PASSWORD=$(env_val PG_PASSWORD)
SMB_PASSWORD=$(env_val SMB_PASSWORD)
KEY1=$(env_val ACCESS_KEY)
ROLE_PASSWORD=$(openssl rand -hex 12)
ADMIN_META="postgres://floppy_admin:$PG_PASSWORD@postgres:5432/$DB?sslmode=disable"
ROLE_META="postgres://$ROLE:$ROLE_PASSWORD@postgres:5432/$DB?sslmode=disable"

psql_db() { docker compose exec -T postgres psql -q -U floppy_admin -d "$1" -v ON_ERROR_STOP=1 "${@:2}"; }
garage() { docker compose exec -T garage /garage "$@"; }
admin_juicefs() { docker compose run --rm -T --no-deps -e META_URL="$ADMIN_META" --entrypoint juicefs drive "$@"; }
say() { printf '\n## %s\n' "$*"; }

# Before every stop or recreation of drive: a server that goes away under a mount
# brings up macOS's "Server connections interrupted" dialog (.claude/rules/spike.md).
unmount() {
  for _ in 1 2 3 4 5; do
    /sbin/mount | grep -q " on $MNT " || { rmdir "$MNT" 2>/dev/null || true; return 0; }
    /sbin/umount -f "$MNT" 2>/dev/null || sleep 2
  done
  echo "meta-access: cannot unmount $MNT" >&2
  return 1
}
mount_share() {
  mkdir -p "$MNT"
  for _ in $(seq 1 30); do
    /sbin/mount_smbfs "//drive:$SMB_PASSWORD@localhost:4503/FloppySpike" "$MNT" 2>/dev/null && return 0
    sleep 2
  done
  echo "meta-access: the share did not come up" >&2
  docker compose logs --tail 30 drive >&2
  return 1
}

# Writes a fresh 1 MB file through the share; prints ok or the error, within $1 seconds.
try_write() {
  python3 - "$MNT" "$1" <<'PY'
import os, sys, subprocess, time
path, limit = os.path.join(sys.argv[1], f"probe-{time.time_ns()}.bin"), float(sys.argv[2])
code = f"open({path!r}, 'wb').write(os.urandom(1 << 20))"
try:
    r = subprocess.run([sys.executable, "-c", "import os; " + code], timeout=limit, capture_output=True, text=True)
    print("ok" if r.returncode == 0 else "error: " + (r.stderr.strip().splitlines() or ["?"])[-1])
except subprocess.TimeoutExpired:
    print(f"no answer within {limit:.0f} s")
PY
}

restore() {
  garage bucket allow --read --write --owner floppy --key "$KEY1" >/dev/null 2>&1 || true
  [ -n "${KEY2:-}" ] && garage key delete --yes "$KEY2" >/dev/null 2>&1 || true
}
trap restore EXIT

say "Volume on PostgreSQL, formatted by the admin"
unmount
docker compose rm -sf drive >/dev/null
psql_db floppy -c "DROP DATABASE IF EXISTS $DB" -c "DROP ROLE IF EXISTS $ROLE" -c "CREATE DATABASE $DB" >/dev/null
admin_juicefs format "$ADMIN_META" access-test --storage s3 --bucket http://toxiproxy:4501/floppy \
  --access-key "$KEY1" --secret-key "$(env_val SECRET_KEY)" --trash-days 0 2>&1 | tail -1

say "1. Mount with a role limited to the JuiceFS tables"
psql_db "$DB" <<SQL
CREATE ROLE $ROLE LOGIN PASSWORD '$ROLE_PASSWORD';
GRANT CONNECT ON DATABASE $DB TO $ROLE;
GRANT USAGE ON SCHEMA public TO $ROLE;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO $ROLE;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO $ROLE;
SQL
META_URL="$ROLE_META" VOLUME_NAME=access-test docker compose up -d --force-recreate drive >/dev/null
mount_share
echo "write as the limited role: $(try_write 60)"
echo "files on the volume: $(find "$MNT" -name 'probe-*' | wc -l | tr -d ' ')"

say "2. What the limited role can read"
psql_db "$DB" -c "SET ROLE $ROLE" -t -c "SELECT CASE WHEN value LIKE '%SecretKey%' THEN 'format readable, it holds the S3 secret (encrypted with the volume UUID)' ELSE 'format readable, no secret in it' END FROM jfs_setting WHERE name = 'format'"
psql_db "$DB" -c "SET ROLE $ROLE" -c "CREATE TABLE should_fail (x int)" 2>&1 | tail -1 | sed 's/^/DDL as the limited role: /'

say "3. Key rotation with the client mounted"
key_out=$(garage key create floppy-spike-rotated)
KEY2=$(echo "$key_out" | awk '/Key ID/ {print $NF}')
SECRET2=$(echo "$key_out" | awk '/Secret key/ {print $NF}')
garage bucket allow --read --write --owner floppy --key "$KEY2" >/dev/null
admin_juicefs config "$ADMIN_META" --access-key "$KEY2" --secret-key "$SECRET2" --yes 2>&1 | tail -1
garage bucket deny --read --write --owner floppy --key "$KEY1" >/dev/null
start=$(date +%s)
while :; do
  r=$(try_write 30)
  elapsed=$(( $(date +%s) - start ))
  echo "t+${elapsed}s: $r"
  [ "$r" = ok ] && break
  [ "$elapsed" -ge 180 ] && { echo "the client never picked up the new key"; break; }
  sleep 5
done

say "4. Drop the role while the client is mounted"
psql_db "$DB" <<SQL
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM $ROLE;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM $ROLE;
REVOKE USAGE ON SCHEMA public FROM $ROLE;
REVOKE CONNECT ON DATABASE $DB FROM $ROLE;
SELECT count(pg_terminate_backend(pid)) AS sessions_ended FROM pg_stat_activity WHERE usename = '$ROLE';
DROP ROLE $ROLE;
SQL
start=$(date +%s)
echo "write after the drop: $(try_write 60) (after $(( $(date +%s) - start )) s)"
listing=$(python3 -c "
import os, sys, signal
signal.alarm(60)
try: print(len(os.listdir(sys.argv[1])), 'entries')
except Exception as e: print('error:', e)
" "$MNT" 2>&1)
echo "listing after the drop: $listing"
docker compose logs --tail 5 drive 2>&1 | grep -i -m 3 "error\|denied\|fatal" | cut -c1-200 || true

say "Admin view after the drop"
unmount
docker compose rm -sf drive >/dev/null
admin_juicefs status "$ADMIN_META" 2>/dev/null | grep -c '"Sid"' | sed 's/^/sessions left in the metadata: /'
admin_juicefs fsck "$ADMIN_META" --path / 2>&1 | tail -2
echo "done; the spike key is allowed on the bucket again"
