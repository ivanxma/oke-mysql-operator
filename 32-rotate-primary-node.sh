#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/clusterset_common.sh"
if [[ ${1:-} != --help && ${1:-} != -h ]]; then connect_kubernetes; fi
python3 "$SCRIPT_DIR/lib/operations.py" rotate "$@"
