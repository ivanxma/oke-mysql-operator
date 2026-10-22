#!/usr/bin/env bash
# Read-only subnet/VCN/resolver discovery; never creates or attaches resources.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-discover-dns-view.sh'
  echo 'Find the resolver default view for the configured private machine subnet.'
  echo 'Requires read virtual-network-family, read dns-resolvers and read dns-views in the selected compartment.'
  exit 0
fi
[[ $# -eq 0 ]] || fail 'This step takes no arguments.'
load_config; load_host; validate_cluster
remote_python "$COMMUNITY_DIR/lib/discover-dns-view.py" "$ADMIN_WORKDIR" "$OCI_COMPARTMENT_ID" \
  "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$MACHINE_CIDR" "$NODE_IP" "$CLUSTER_NAME.$BASE_DOMAIN"
