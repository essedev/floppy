#!/usr/bin/env bash
# A local Floppy instance a real device on this Mac can use: deploy/docker-compose.yml with
# deploy/local/compose.yml (Traefik, Garage in managed mode, a throwaway CA for
# *.meta.floppy.test, console mail). The web and the API are http://127.0.0.1:4583, the
# address to give the app; sign-in codes and links are in the server's log (`mail`).
#
# STACK=manual runs a second instance beside it, in manual storage mode against a real S3
# (each disk's bucket and key pasted at creation): project floppy-local-manual, its own state
# (deploy/local/.state-manual), networks and ports (Portsage range floppy-local-manual,
# 4590-4599: the web and the API on http://127.0.0.1:4593, the disks' metadata on 4590), no
# Garage. The organization's default endpoint and region come from LOCAL_S3_ENDPOINT and
# LOCAL_S3_REGION at the first `up` (default OVH Milan: https://s3.eu-south-mil.io.cloud.ovh.net,
# eu-south-mil). Every command below takes STACK.
#
#   deploy/local/stack.sh up                       build, start, bootstrap; prints the addresses
#   deploy/local/stack.sh down                     stop (volumes kept: data and the organization stay)
#   deploy/local/stack.sh status                   the containers
#   deploy/local/stack.sh mail <email>             the last mail to that address (code, link)
#   deploy/local/stack.sh hosts [--remove]         the disks' names in a device VM's /etc/hosts
#
# `hosts` maps s3.floppy.test and every disk's <label>.meta.floppy.test to the Mac (Lima's
# host.lima.internal) inside the VM of FLOPPY_HOME (default ~/.floppy) and FLOPPY_VM (default
# floppy), in a marked block; run it again after creating a disk. Lima's own host resolver
# takes no wildcard, and the names must stay names: the TLS proxy routes by SNI.
#
# Secrets (database, meta tokens, Garage) live in deploy/local/.state/local.env, outside git,
# never printed. The bootstrap admin is admin@floppy.test. Needs Docker; the patched juicefs
# from spike/juicefs-replica/dist when built, else Compose builds it.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
deploy=$(dirname "$here")
root=$(dirname "$deploy")
stack="${STACK:-managed}"
case "$stack" in
  managed)
    project=floppy-local state="$here/.state" subnet=10.250.56.0/24 internal=10.250.57.0/24
    meta_port=4580 web_port=4583 ;;
  manual)
    project=floppy-local-manual state="$here/.state-manual" subnet=10.250.58.0/24 internal=10.250.59.0/24
    meta_port=4590 web_port=4593 ;;
  *) echo "STACK is managed (the default) or manual" >&2; exit 2 ;;
esac
env_file="$state/local.env"
network="$project-proxy"
server="http://127.0.0.1:$web_port"
admin=admin@floppy.test

dc() {
  local profiles=()
  if [ "$stack" = managed ]; then profiles=(--profile garage); fi
  docker compose --progress quiet -p "$project" --env-file "$env_file" ${profiles[@]+"${profiles[@]}"} \
    -f "$deploy/docker-compose.yml" -f "$here/compose.yml" "$@"
}

token() { openssl rand -base64 33 | tr '+/' '-_' | tr -d '=\n'; }

# The CA and the wildcard, made once: devices keep the CA from the server's answer.
write_certs() {
  local tls="$state/tls" dyn="$state/dynamic"
  mkdir -p "$tls" "$dyn"
  if [ ! -s "$tls/ca.crt" ] || ! openssl x509 -checkend 86400 -noout -in "$tls/meta.crt" >/dev/null 2>&1; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=Floppy local CA" \
      -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -keyout "$tls/ca.key" -out "$tls/ca.crt" 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj "/CN=floppy.test" -keyout "$tls/meta.key" -out "$tls/meta.csr" 2>/dev/null
    printf '%s\n' "subjectAltName=DNS:floppy.test,DNS:*.meta.floppy.test" \
      "extendedKeyUsage=serverAuth,clientAuth" "basicConstraints=CA:FALSE" \
      "authorityKeyIdentifier=keyid,issuer" "subjectKeyIdentifier=hash" > "$tls/meta.ext"
    openssl x509 -req -in "$tls/meta.csr" -CA "$tls/ca.crt" -CAkey "$tls/ca.key" -CAcreateserial \
      -days 300 -extfile "$tls/meta.ext" -out "$tls/meta.crt" 2>/dev/null
  fi
  cp "$tls/meta.crt" "$tls/meta.key" "$dyn/"
  chmod 644 "$dyn/meta.crt" "$dyn/meta.key" # read by Traefik's user; a throwaway CA
  printf '%s\n' "tls:" "  stores:" "    default:" "      defaultCertificate:" \
    "        certFile: /etc/traefik/dynamic/meta.crt" "        keyFile: /etc/traefik/dynamic/meta.key" > "$dyn/tls.yml"
}

# The manual stack's Traefik: deploy/local/traefik.yml on its own network and project.
write_traefik() {
  [ "$stack" = manual ] || return 0
  sed -e "s/floppy-local-proxy/$network/" -e "s/\`floppy-local\`/\`$project\`/" \
    "$here/traefik.yml" > "$state/traefik.yml"
}

# The storage lines of the env file: Garage behind the adapter, or a real S3 in manual mode.
storage_env() {
  if [ "$stack" = managed ]; then
    local garage_token
    garage_token=$(openssl rand -hex 32)
    cat <<EOF
FLOPPY_BOOTSTRAP_S3_ENDPOINT=http://s3.floppy.test:4582
FLOPPY_BOOTSTRAP_S3_REGION=garage
FLOPPY_BOOTSTRAP_STORAGE_MODE=managed
FLOPPY_BOOTSTRAP_STORAGE_PROVIDER=garage
FLOPPY_BOOTSTRAP_STORAGE_ADMIN_CREDENTIALS={"adminUrl":"http://garage:3903","adminToken":"$garage_token"}
LOCAL_GARAGE_RPC_SECRET=$(openssl rand -hex 32)
LOCAL_GARAGE_ADMIN_TOKEN=$garage_token
EOF
  else
    cat <<EOF
FLOPPY_BOOTSTRAP_S3_ENDPOINT=${LOCAL_S3_ENDPOINT:-https://s3.eu-south-mil.io.cloud.ovh.net}
FLOPPY_BOOTSTRAP_S3_REGION=${LOCAL_S3_REGION:-eu-south-mil}
FLOPPY_BOOTSTRAP_STORAGE_MODE=manual
LOCAL_STATE=$(basename "$state")
LOCAL_TRAEFIK_YML=./local/$(basename "$state")/traefik.yml
LOCAL_META_PORT=$meta_port
LOCAL_WEB_PORT=$web_port
EOF
  fi
}

# Kept between runs: the database volume keeps the passwords it was created with.
write_env() {
  mkdir -p "$state"
  local ca
  ca=$(awk '{printf "%s\\n", $0}' "$state/tls/ca.crt")
  if [ ! -f "$env_file" ]; then
    (umask 077; cat > "$env_file" <<EOF
FLOPPY_DOMAIN=floppy.test
FLOPPY_PROXY_NETWORK=$network
FLOPPY_INTERNAL_SUBNET=$internal
FLOPPY_TRUSTED_PROXIES=$subnet
FLOPPY_META_CERT_RESOLVER=
FLOPPY_SECRET_KEY=$(openssl rand -base64 32)
FLOPPY_DB_OWNER_PASSWORD=$(token)
FLOPPY_DB_APP_PASSWORD=$(token)
FLOPPY_META_TOKEN=$(token)
FLOPPY_META_ROUTES_TOKEN=floppy-local-routes-token-not-a-secret-01
FLOPPY_SMTP_HOST=smtp.invalid
FLOPPY_SMTP_SENDER=Floppy <floppy@floppy.test>
FLOPPY_BOOTSTRAP_ORGANIZATION_NAME=Studio Locale
FLOPPY_BOOTSTRAP_ORGANIZATION_SLUG=studio-locale
FLOPPY_BOOTSTRAP_ADMIN_EMAIL=$admin
$(storage_env)
EOF
    )
  fi
  # The CA in one line, \n for newlines (the server's FLOPPY_META_CA_PEM reads them back).
  local tmp
  tmp=$(mktemp "$state/env.XXXXXX")
  grep -v '^LOCAL_META_CA_PEM=' "$env_file" > "$tmp" || true
  printf 'LOCAL_META_CA_PEM=%s\n' "$ca" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$env_file"
}

garage() { dc exec -T garage /garage "$@"; }

cmd_up() {
  docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 1; }
  write_certs
  write_env
  write_traefik
  local dist="${FLOPPY_JUICEFS_DIST:-$root/spike/juicefs-replica/dist}"
  if [ "${dist#service:}" = "$dist" ] && [ ! -f "$dist/SHA256SUMS" ]; then dist=service:juicefs; fi
  export FLOPPY_JUICEFS_DIST="$dist"
  docker network inspect "$network" >/dev/null 2>&1 || docker network create --subnet "$subnet" "$network" >/dev/null
  # Garage first, with its layout: the bootstrap asks its admin API for the control plane's key.
  if [ "$stack" = managed ]; then
    dc up -d garage traefik >/dev/null
    for _ in $(seq 1 30); do garage status >/dev/null 2>&1 && break; sleep 1; done
    if garage status 2>/dev/null | grep -q "NO ROLE ASSIGNED"; then
      local node
      node=$(garage status 2>/dev/null | awk '/NO ROLE ASSIGNED/ {print $1; exit}')
      garage layout assign -z local -c 10G "$node" >/dev/null
      garage layout apply --version 1 >/dev/null
    fi
  fi
  dc up -d --build --wait >/dev/null
  echo "Floppy local stack up ($stack):"
  echo "  server (app and admin web)  $server   admin: $admin"
  echo "  disks' metadata             rediss://<label>.meta.floppy.test:$meta_port (CA: deploy/local/$(basename "$state")/tls/ca.crt)"
  if [ "$stack" = managed ]; then
    echo "  S3 (Garage, managed)        http://s3.floppy.test:4582"
  else
    echo "  S3 (manual)                 $(sed -n 's/^FLOPPY_BOOTSTRAP_S3_ENDPOINT=//p' "$env_file"), a bucket and key per disk"
  fi
  echo "  codes and links             deploy/local/stack.sh mail <email>"
  echo "  a device VM's names         deploy/local/stack.sh hosts (after creating a disk)"
}

cmd_down() {
  [ -f "$env_file" ] || { echo "not set up" >&2; exit 0; }
  dc down >/dev/null
  docker network rm "$network" >/dev/null 2>&1 || true
  echo "Floppy local stack down ($stack, volumes kept)."
}

# The newest console mail to an address, from the server's log.
cmd_mail() {
  local to="${1:?usage: stack.sh mail <email>}"
  dc logs --no-log-prefix server 2>&1 | awk -v to="To: $to" '
    $0 == to { buf = $0 "\n"; n = 14; next }
    n > 0 { buf = buf $0 "\n"; n-- }
    END { printf "%s", buf }'
}

# The names the device VM must resolve to the Mac: the S3 endpoint and each instance.
names() {
  if [ "$stack" = managed ]; then printf 's3.floppy.test\n'; fi
  # shellcheck disable=SC2016 # Python, not shell
  dc exec -T meta python -c '
import json, os, re, urllib.request
req = urllib.request.Request("http://127.0.0.1:8080/v1/traefik",
    headers={"Authorization": "Bearer " + os.environ["FLOPPY_META_ROUTES_TOKEN"]})
routes = json.load(urllib.request.urlopen(req, timeout=5)).get("tcp", {}).get("routers", {})
for r in routes.values():
    m = re.search(r"HostSNI\(`([^`]+)`\)", r["rule"])
    if m:
        print(m.group(1))
'
}

cmd_hosts() {
  local home="${FLOPPY_HOME:-$HOME/.floppy}" vm="${FLOPPY_VM:-floppy}" list=""
  local limactl="$home/lima/bin/limactl"
  [ -x "$limactl" ] || { echo "no engine in $home (FLOPPY_HOME)" >&2; exit 1; }
  if [ "${1:-}" != --remove ]; then list=$(names | sort -u); fi
  # The block, one per stack, is rewritten whole each time; the rest of /etc/hosts stays.
  # shellcheck disable=SC2016 # expanded inside the VM
  printf '%s\n' "$list" | LIMA_HOME="$home/vms" "$limactl" shell --workdir / "$vm" sudo env "mark=$project" sh -c '
    ip=$(getent hosts host.lima.internal | cut -d" " -f1)
    [ -n "$ip" ] || { echo "the VM does not resolve host.lima.internal" >&2; exit 1; }
    sed "/^# $mark begin$/,/^# $mark end$/d" /etc/hosts > /etc/hosts.floppy-local
    names=$(grep . || true)
    if [ -n "$names" ]; then
      { echo "# $mark begin"; printf "%s\n" "$names" | sed "s/^/$ip /"; echo "# $mark end"; } >> /etc/hosts.floppy-local
    fi
    cat /etc/hosts.floppy-local > /etc/hosts && rm -f /etc/hosts.floppy-local'
  if [ -n "$list" ]; then
    echo "$(printf '%s\n' "$list" | wc -l | tr -d ' ') names mapped to the Mac in VM $vm ($home)"
  else
    echo "block removed from VM $vm ($home)"
  fi
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) dc ps ;;
  mail) shift; cmd_mail "$@" ;;
  hosts) shift; cmd_hosts "$@" ;;
  *) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
