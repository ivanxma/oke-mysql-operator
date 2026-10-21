#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./08-create-replica-cluster.sh (native ClusterSet bootstrap into the second namespace)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes; worker_check
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" replica
