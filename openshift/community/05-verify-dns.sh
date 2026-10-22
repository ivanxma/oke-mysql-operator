#!/usr/bin/env bash
# Read-only host-side DNS acceptance, optionally polling for propagation.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
wait_seconds=0 interval=15
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-verify-dns.sh [--wait <seconds>] [--interval <seconds>]'
  echo 'Checks admin and target DNS. No OCI, DNS, host configuration or cluster changes.'
  echo 'Default: check once. Exit 0=all checks pass; 1=failed; 2=invalid configuration.'
  exit 0
fi
while [[ $# -gt 0 ]]; do
  case $1 in
    --wait|--interval)
      [[ $# -ge 2 && $2 =~ ^[0-9]+$ ]] || fail 'Supply a non-negative number of seconds.'
      if [[ $1 == --wait ]]; then wait_seconds=$2; else interval=$2; fi
      shift 2 ;;
    *) fail 'Use --wait <seconds> and/or --interval <seconds>.' ;;
  esac
done
load_config; load_host; validate_cluster
need python3; need ssh
exec python3 "$COMMUNITY_DIR/lib/verify-dns.py" run "$STATE_DIR/ssh_config" \
  "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$wait_seconds" "$interval"
