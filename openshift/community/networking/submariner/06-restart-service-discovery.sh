#!/usr/bin/env bash
# Reload the existing broker CA after validating credentials and saved cluster identity.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $# -eq 0 || ( $# -eq 1 && $1 == --apply ) ]] || { echo 'Usage: 06-restart-service-discovery.sh [--apply]' >&2; exit 1; }
exec python3 "$SUBMARINER_DIR/lib/restart-service-discovery.py" "$@"
