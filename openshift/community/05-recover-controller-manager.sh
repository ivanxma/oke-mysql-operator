#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
mode=preview
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-recover-controller-manager.sh [--apply]'
  echo 'Default preview; --apply requests only a failed controller-manager installer rollout after health/identity checks.'
  exit 0
fi
if [[ ${1:-} == --apply ]]; then mode=apply; shift; fi
[[ $# -eq 0 ]] || fail 'Use --apply or no arguments for preview.'
load_config; load_host; validate_cluster
remote_python "$COMMUNITY_DIR/lib/diagnose-control-plane.py" "$ADMIN_WORKDIR" "$KUBECONFIG" \
  "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$mode"
