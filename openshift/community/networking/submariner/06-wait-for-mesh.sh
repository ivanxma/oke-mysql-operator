#!/usr/bin/env bash
# Bounded read-only readiness polling; never rejoins a cluster.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
timeout=600 interval=15
while [[ $# -gt 0 ]]; do
 case $1 in
  --timeout) [[ $# -ge 2 ]] || exit 1; timeout=$2; shift 2 ;;
  --interval) [[ $# -ge 2 ]] || exit 1; interval=$2; shift 2 ;;
  *) echo 'Usage: 06-wait-for-mesh.sh [--timeout 600] [--interval 15]' >&2; exit 1 ;;
 esac
done
exec python3 "$SUBMARINER_DIR/lib/mesh-readiness.py" wait "$timeout" "$interval"
