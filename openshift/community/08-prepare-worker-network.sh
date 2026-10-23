#!/usr/bin/env bash
# Dedicated OKD node NSG; shared security lists and routes are unchanged.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./08-prepare-worker-network.sh [--worker-ip <assigned-private-IP>] [--apply]'
  echo 'Default: read-only preview. Apply creates/reuses a cluster-owned NSG for intra-cluster OVN UDP 6081 and attaches it to the control-plane VNIC.'
  echo 'Select the resulting NSG when manually creating each worker. No shared security lists, routes or IAM are modified.'
  exit 0
fi
action=preview worker_ip=''
while [[ $# -gt 0 ]]; do
  case $1 in
    --apply) action=apply; shift ;;
    --worker-ip) [[ $# -ge 2 ]] || fail 'Supply the assigned worker IP.'; worker_ip=$2; shift 2 ;;
    *) fail 'Use --help.' ;;
  esac
done
load_config; validate_cluster
[[ $ADMIN_HOST == 127.0.0.1 ]] || fail 'Run on the existing jump host from its generated execution folder.'
interpreter=python3
if [[ -x $ADMIN_WORKDIR/oci-sdk/bin/python ]]; then interpreter=$ADMIN_WORKDIR/oci-sdk/bin/python; fi
exec "$interpreter" "$COMMUNITY_DIR/lib/prepare-worker-network.py" "$ADMIN_WORKDIR" "$STATE_DIR" "$KUBECONFIG" \
  "$OCI_COMPARTMENT_ID" "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$MACHINE_CIDR" "$action" "$worker_ip"
