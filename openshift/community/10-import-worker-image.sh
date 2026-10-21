#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./10-import-worker-image.sh (upload/import only; rerun to resume an importing image)'
  exit 0
fi
load_config; load_host; validate_cluster
[[ $OCI_AUTH == instance_principal ]] || fail 'Only instance-principal authentication is allowed.'
for variable in OCI_BUCKET OCI_NAMESPACE OCI_REGION OCI_COMPARTMENT_ID OCI_COMPARTMENT_NAME; do
  [[ -n ${!variable:-} ]] || fail "Set $variable in the private configuration."
done
remote_python "$COMMUNITY_DIR/lib/import-oci-image.py" "$ADMIN_WORKDIR" "$OCI_COMPARTMENT_ID" \
  "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$OCI_NAMESPACE" "$OCI_BUCKET" \
  "$CLUSTER_NAME.$BASE_DOMAIN" "$OKD_VERSION" worker
