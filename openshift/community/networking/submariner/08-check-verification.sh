#!/usr/bin/env bash
# Check current verification evidence and cleanup; no resource changes.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $# -eq 0 ]] || { echo 'Usage: 08-check-verification.sh' >&2; exit 1; }
exec python3 "$SUBMARINER_DIR/lib/mesh-readiness.py" report
