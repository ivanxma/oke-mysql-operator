#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./07-verify-primary-cluster.sh (ONLINE, PVC/Router readiness and worker placement)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes; worker_check
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" verify-primary
export MYSQL_VERIFY_PRIMARY_ONLY=true
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" placement
