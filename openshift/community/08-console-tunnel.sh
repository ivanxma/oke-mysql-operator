#!/usr/bin/env bash
# Optional local SOCKS tunnel for browsers without private routing/DNS.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./08-console-tunnel.sh (optional SOCKS_PORT, default 1080; stop with Ctrl-C)'
  exit 0
fi
[[ $# == 0 ]] || fail 'This script takes no arguments; use the private environment configuration.'
port=${SOCKS_PORT:-1080}
[[ $port =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || fail 'SOCKS_PORT must be an integer from 1 to 65535.'
load_config; load_host; validate_cluster
printf 'Browser SOCKS5 proxy: 127.0.0.1:%s; enable DNS through the proxy.\n' "$port"
printf 'Console: https://console-openshift-console.apps.%s.%s\n' "$CLUSTER_NAME" "$BASE_DOMAIN"
printf 'Keep this terminal open. Stop the tunnel with Ctrl-C.\n'
exec ssh -F "$STATE_DIR/ssh_config" -o ExitOnForwardFailure=yes \
  -N -D "127.0.0.1:$port" okd-admin
