#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
interval=15 timeout=3600 mode=wait
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./05-wait-for-control-plane.sh [--status-only] [--interval <seconds>] [--timeout <seconds>]'
  echo 'Read-only monitoring: DNS, API, nodes, operators and streamed installer progress.'
  echo 'Default: status every 15 seconds, monitoring timeout 3600 seconds.'
  echo 'Ctrl-C stops monitoring; it does not stop the VM installation.'
  exit 0
fi
while [[ $# -gt 0 ]]; do
  case $1 in
    --status-only) mode=status; shift ;;
    --interval|--timeout)
      [[ $# -ge 2 && $2 =~ ^[0-9]+$ ]] || fail 'Supply a positive number of seconds.'
      if [[ $1 == --interval ]]; then interval=$2; else timeout=$2; fi
      shift 2 ;;
    *) fail 'Use --status-only, --interval <seconds> or --timeout <seconds>.' ;;
  esac
done
load_config; load_host; validate_cluster
printf 'Connecting to admin host for read-only installation monitoring...\n'
remote_python "$COMMUNITY_DIR/lib/monitor-control-plane.py" "$ADMIN_WORKDIR" "$KUBECONFIG" \
  "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$interval" "$timeout" "$mode"
