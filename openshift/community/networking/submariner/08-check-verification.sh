#!/usr/bin/env bash
# Check current verification evidence and cleanup; no resource changes.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
wait_seconds=120 interval=5
while [[ $# -gt 0 ]]; do
 case $1 in
  --wait) [[ $# -ge 2 ]] || exit 1; wait_seconds=$2; shift 2 ;;
  --interval) [[ $# -ge 2 ]] || exit 1; interval=$2; shift 2 ;;
  *) echo 'Usage: 08-check-verification.sh [--wait 120] [--interval 5]' >&2; exit 1 ;;
 esac
done
exec python3 "$SUBMARINER_DIR/lib/mesh-readiness.py" report "$wait_seconds" "$interval"
