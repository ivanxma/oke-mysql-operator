#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./06-create-primary-cluster.sh (creates primary and credentials; password file or secure prompt)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes; worker_check; resolve_storage_class
k --request-timeout=0 -n "$OPERATOR_NAMESPACE" rollout status deployment/mysql-operator --timeout=120s
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" primary
