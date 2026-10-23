#!/usr/bin/env bash
# Recover only the selected target's saved SSH identity after independent verification.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
fingerprint='' action=preview
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-refresh-target-host-key.sh --fingerprint SHA256:<console-fingerprint> [--apply]'
  echo 'First obtain the target ED25519 fingerprint through its OCI serial console:'
  echo 'sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'
  echo 'Default: verify fingerprint and SSH access; no saved identity changes.'
  echo '--apply: back up private known_hosts and replace only the registered target entry.'
  exit 0
fi
while [[ $# -gt 0 ]]; do
  case $1 in
    --fingerprint) [[ $# -ge 2 ]] || fail 'Supply the independently verified fingerprint.'; fingerprint=$2; shift 2 ;;
    --apply) action=apply; shift ;;
    *) fail 'Use --fingerprint SHA256:<console-fingerprint> [--apply].' ;;
  esac
done
[[ -n $fingerprint ]] || fail 'Obtain the target fingerprint through the OCI serial console, then supply --fingerprint.'
load_config; load_host; validate_cluster
need ssh; need ssh-keygen; need python3
exec python3 "$COMMUNITY_DIR/lib/refresh-target-host-key.py" \
  "$STATE_DIR" "$NODE_IP" "$fingerprint" "$action"
