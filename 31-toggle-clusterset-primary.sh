#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/clusterset_common.sh"
# Share cluster3's configured name with the c2 deployment scripts.
if [[ -f "${MYSQLK8S_CLUSTER3_CONFIG:-$SCRIPT_DIR/oke2/cluster3.env}" ]]; then
    set -a
    source "${MYSQLK8S_CLUSTER3_CONFIG:-$SCRIPT_DIR/oke2/cluster3.env}"
    set +a
fi
export CLUSTER3_NAME="${CLUSTER3_NAME:-mycluster3}"
if [[ ${1:-} != --help && ${1:-} != -h ]]; then connect_kubernetes; fi
python3 "$SCRIPT_DIR/lib/operations.py" toggle "$@"
