#!/usr/bin/env bash
# Explicitly confirmed local-media cleanup; cloud resources are read-only.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
role=control-plane
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./04-cleanup-local-iso.sh [--worker]'
  echo 'Reports disk/media, verifies private upload and AVAILABLE image, then asks DELETE.'
  echo 'Deletes only the generated installation ISO; retains base ISO, credentials and receipts.'
  exit 0
fi
if [[ ${1:-} == --worker ]]; then role=worker; shift; fi
[[ $# -eq 0 ]] || fail 'Use --worker or no arguments.'
load_config preparation; load_host; validate_cluster preparation
args=("$ADMIN_WORKDIR" "$OCI_COMPARTMENT_ID" "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$OCI_NAMESPACE" "$OCI_BUCKET" "$CLUSTER_NAME.$BASE_DOMAIN" "$OKD_VERSION" "$role")
remote_python "$COMMUNITY_DIR/lib/cleanup-local-iso.py" "${args[@]}" inspect
[[ -t 0 ]] || fail 'Interactive confirmation is required; no ISO deleted.'
read -r -p 'Delete ONLY the verified generated ISO shown above? Type DELETE: ' answer
if [[ $answer != DELETE ]]; then echo 'Cancelled; no ISO deleted.'; exit 0; fi
# Recheck all evidence after confirmation, before deleting the single file.
remote_python "$COMMUNITY_DIR/lib/cleanup-local-iso.py" "${args[@]}" delete
