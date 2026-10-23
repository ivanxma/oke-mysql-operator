#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
mode=diagnose
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-diagnose-control-plane.sh [--node-bootstrap]'
  echo 'Read-only authenticated API, operator, revision and certificate-registration diagnostics. Saves a private report.'
  echo '--node-bootstrap: inspect target listeners, services and container logs over SSH when API queries fail.'
  exit 0
fi
node_bootstrap=false
if [[ ${1:-} == --node-bootstrap ]]; then node_bootstrap=true; shift; fi
[[ $# -eq 0 ]] || fail 'Use --node-bootstrap or no arguments.'
load_config; load_host; validate_cluster
if $node_bootstrap; then
  exec python3 "$COMMUNITY_DIR/lib/node-bootstrap-diagnostics.py" "$STATE_DIR" "$NODE_IP" "$OCI_COMPARTMENT_ID"
fi
remote_python "$COMMUNITY_DIR/lib/diagnose-control-plane.py" "$ADMIN_WORKDIR" "$KUBECONFIG" \
  "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$mode"
