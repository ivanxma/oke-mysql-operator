#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
mode=diagnose
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-diagnose-control-plane.sh'
  echo 'Read-only authenticated API, operator, revision and certificate-registration diagnostics. Saves a private report.'
  exit 0
fi
[[ $# -eq 0 ]] || fail 'No arguments expected.'
load_config; load_host; validate_cluster
remote_python "$COMMUNITY_DIR/lib/diagnose-control-plane.py" "$ADMIN_WORKDIR" "$KUBECONFIG" \
  "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$mode"
