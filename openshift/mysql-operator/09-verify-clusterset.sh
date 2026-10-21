#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./09-verify-clusterset.sh (ClusterSet HEALTHY, replica read-only, worker placement)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes; worker_check
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" verify
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" placement

if [[ $STORAGE_CLASS == mysql-oci-bv && $REPLICA_STORAGE_CLASS == mysql-oci-bv ]]; then
  interpreter=$(oci_python) || die 'OCI SDK/YAML interpreter is required to audit OCI database volumes.'
  "$interpreter" "$MYSQL_OPENSHIFT_DIR/lib/oci_storage.py" --audit
fi
