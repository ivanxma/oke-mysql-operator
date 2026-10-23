#!/usr/bin/env bash
# Read-only OCI/PVC/PV/worker attachment audit; saves private evidence.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
exec "$MYSQL_OCI_PYTHON" "$MYSQL_REGIONAL_DIR/lib/regional.py" audit
