#!/usr/bin/env bash
# Run only when the operator has chosen and verified the actual target VM address.
set -euo pipefail
set +x
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-register-node-ip.sh <explicit-private-IP> --assigned-to-target'
  echo 'Register only an operator-selected address; does not choose/reserve IPs, create VMs or change DNS.'
  exit 0
fi
[[ $# -eq 2 && $2 == --assigned-to-target ]] || fail 'Supply your explicit private IP and --assigned-to-target after manually verifying that this IP is assigned to the intended new target VM.'
selected_ip=$1
load_config preparation; load_host
umask 077
receipt=$(mktemp "$STATE_DIR/node-ip-receipt.XXXXXX")
trap 'rm -f -- "$receipt"' EXIT
remote_python "$COMMUNITY_DIR/lib/register-node-ip.py" "$ADMIN_WORKDIR" "$OCI_COMPARTMENT_ID" \
  "$OCI_REGION" "$CLUSTER_NAME" "$BASE_DOMAIN" "$MACHINE_CIDR" "$selected_ip" > "$receipt"
python3 "$COMMUNITY_DIR/lib/register-node-ip.py" --ingest "$receipt" "$STATE_DIR" "$ADMIN_WORKDIR" \
  "$OCI_COMPARTMENT_ID" "$OCI_REGION" "$CLUSTER_NAME" "$BASE_DOMAIN" "$MACHINE_CIDR" "$selected_ip"
