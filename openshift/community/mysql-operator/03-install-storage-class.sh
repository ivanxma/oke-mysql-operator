#!/usr/bin/env bash
# Dedicated storage step; may drain/reboot the selected worker for OCI device naming.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $# -eq 0 ]] || { echo 'Usage: 03-install-storage-class.sh (selected generated regional profile)' >&2; exit 1; }
exec "$MYSQL_OCI_PYTHON" "$MYSQL_REGIONAL_DIR/lib/regional.py" storage
