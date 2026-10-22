#!/usr/bin/env bash
# Provision/validate image staging and register it in the selected environment.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./02-create-bucket.sh (after step 01, before 02-download-okd.sh)'
  echo 'Uses instance principal; generates private regional bucket environment state.'
  exit 0
fi
[[ $# -eq 0 ]] || fail 'Configure the bucket through the private environment, not command arguments.'
load_config preparation; load_host
[[ $OCI_AUTH == instance_principal ]] || fail 'Only instance-principal authentication is allowed.'
for variable in OCI_REGION OCI_COMPARTMENT_ID OCI_COMPARTMENT_NAME CLUSTER_NAME; do
  [[ -n ${!variable:-} ]] || fail "Set $variable in the private configuration."
done
umask 077
mkdir -p "$STATE_DIR"
receipt=$(mktemp "$STATE_DIR/bucket-receipt.XXXXXX")
trap 'rm -f -- "$receipt"' EXIT
remote_python "$COMMUNITY_DIR/lib/prepare-bucket.py" "$ADMIN_WORKDIR" "$OCI_COMPARTMENT_ID" \
  "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$OCI_NAMESPACE" "$OCI_BUCKET" "$CLUSTER_NAME" > "$receipt"
python3 "$COMMUNITY_DIR/lib/prepare-bucket.py" --register "$receipt" "$STATE_DIR" \
  "$OCI_COMPARTMENT_ID" "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$OCI_NAMESPACE" "$OCI_BUCKET" "$ADMIN_WORKDIR"
