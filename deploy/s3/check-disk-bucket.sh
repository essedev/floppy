#!/bin/bash
# Checks a disk's bucket and key as the self-hosting guide sets them up: what JuiceFS does
# works, and the key cannot switch the protection off or erase a version. Never prints the
# key. Every refused request is one that, if allowed, would change nothing or touch only the
# probe objects it writes under floppy-bucket-check/ (kept until their retention ends).
#
# Usage: S3_ENDPOINT=https://... AWS_REGION=<region> deploy/s3/check-disk-bucket.sh <bucket> [key file]
#   The key file holds AWS_ACCESS_KEY_ID= and AWS_SECRET_ACCESS_KEY= lines; without it the
#   two variables come from the environment. Exits 1 if a check fails.
# A new policy can take a few minutes to apply at the provider: run it again if refusals pass.
set -uo pipefail

bucket=${1:?usage: check-disk-bucket.sh <bucket> [key file]}
if [ -n "${2:-}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$2"
  set +a
fi
: "${S3_ENDPOINT:?set S3_ENDPOINT}" "${AWS_ACCESS_KEY_ID:?no AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY:?no AWS_SECRET_ACCESS_KEY}"
export AWS_REGION="${AWS_REGION:-us-east-1}" AWS_PAGER="" AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null

s3() { aws --endpoint-url "$S3_ENDPOINT" s3api "$@"; }
quiet() { s3 "$@" >/dev/null 2>&1; }
failed=0
check() { # <ok|refused> <label> <s3api arguments...>
  local want=$1 label=$2 got=refused
  shift 2
  quiet "$@" && got=ok
  if [ "$got" = "$want" ]; then
    echo "PASS  $label ($got)"
  else
    echo "FAIL  $label: $got, expected $want"
    failed=1
  fi
}

versioning=$(s3 get-bucket-versioning --bucket "$bucket" --query Status --output text 2>/dev/null)
if [ "$versioning" = Enabled ]; then echo "PASS  versioning is on"; else echo "FAIL  versioning is ${versioning:-unreadable}"; failed=1; fi
lock=$(s3 get-object-lock-configuration --bucket "$bucket" --output json 2>/dev/null)
mode=$(printf '%s' "$lock" | python3 -c 'import json, sys
r = json.load(sys.stdin)["ObjectLockConfiguration"].get("Rule", {}).get("DefaultRetention", {})
print(r.get("Mode", ""), r.get("Days", r.get("Years", "")))' 2>/dev/null)
if [ -n "$mode" ] && [ "$mode" != " " ]; then
  echo "PASS  Object Lock: default retention $mode days"
else
  echo "WARN  no Object Lock default retention: this key can erase versions"
fi
if quiet get-bucket-lifecycle-configuration --bucket "$bucket"; then
  echo "PASS  a lifecycle rule exists"
else
  echo "FAIL  no lifecycle rule: old versions would stay billed forever"
  failed=1
fi

key="floppy-bucket-check/probe-$(date +%s)"
version=$(s3 put-object --bucket "$bucket" --key "$key" --body /etc/hosts --query VersionId --output text 2>/dev/null)
if [ -n "$version" ] && [ "$version" != None ]; then echo "PASS  write"; else echo "FAIL  write"; failed=1; fi
check ok "read" get-object --bucket "$bucket" --key "$key" /dev/null
check ok "list" list-objects-v2 --bucket "$bucket" --prefix floppy-bucket-check/ --max-keys 1
check ok "delete (a delete marker)" delete-object --bucket "$bucket" --key "$key"
if [ -n "$mode" ] && [ "$mode" != " " ]; then
  check refused "erase the old version" delete-object --bucket "$bucket" --key "$key" --version-id "$version"
  check refused "erase it bypassing the retention" delete-object --bucket "$bucket" --key "$key" \
    --version-id "$version" --bypass-governance-retention
  rule=$(printf '%s' "$lock" | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin)["ObjectLockConfiguration"]))')
  check refused "change Object Lock (to the same rule)" put-object-lock-configuration --bucket "$bucket" \
    --object-lock-configuration "$rule"
fi
check refused "change versioning (to Enabled)" put-bucket-versioning --bucket "$bucket" \
  --versioning-configuration Status=Enabled
rules=$(s3 get-bucket-lifecycle-configuration --bucket "$bucket" --query '{Rules: Rules}' --output json 2>/dev/null)
if [ -n "$rules" ]; then
  check refused "change the lifecycle (to the same rules)" put-bucket-lifecycle-configuration \
    --bucket "$bucket" --lifecycle-configuration "$rules"
fi
for other in $(s3 list-buckets --query 'Buckets[].Name' --output text 2>/dev/null); do
  [ "$other" = "$bucket" ] && continue
  check refused "read inside $other" list-objects-v2 --bucket "$other" --max-keys 1
done
exit "$failed"
