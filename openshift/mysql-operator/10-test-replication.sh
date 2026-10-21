#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./10-test-replication.sh (writes an audit row in mysql_operator_validation on primary; waits for replica)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes; worker_check
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" verify
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" replication-test
