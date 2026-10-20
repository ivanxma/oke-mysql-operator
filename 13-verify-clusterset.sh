#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/clusterset_common.sh"
connect_kubernetes
python3 "$SCRIPT_DIR/lib/clusterset.py" verify
