#!/usr/bin/env bash
# Remove only the selected target's saved SSH identity, then reconnect interactively.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
fingerprint='' action=preview
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-refresh-target-host-key.sh [--apply] [--fingerprint SHA256:<console-fingerprint>]'
  echo 'Default: preview removal of only the registered target entry; no saved changes.'
  echo '--apply: back up known_hosts, remove target entry, reconnect and ask you to accept its key.'
  echo 'Confirm the configured IP is your intended VM before accepting the replacement key.'
  echo 'Optional --fingerprint verifies identity against a fingerprint obtained via the OCI console.'
  exit 0
fi
while [[ $# -gt 0 ]]; do
  case $1 in
    --fingerprint) [[ $# -ge 2 ]] || fail 'Supply the independently verified fingerprint.'; fingerprint=$2; shift 2 ;;
    --apply) action=apply; shift ;;
    *) fail 'Use [--apply] [--fingerprint SHA256:<console-fingerprint>].' ;;
  esac
done
load_config; load_host; validate_cluster
need ssh; need ssh-keygen; need python3
exec python3 "$COMMUNITY_DIR/lib/refresh-target-host-key.py" \
  "$STATE_DIR" "$NODE_IP" "$fingerprint" "$action"
