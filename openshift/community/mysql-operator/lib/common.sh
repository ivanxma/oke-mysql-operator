#!/usr/bin/env bash
set -euo pipefail
set +x
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/env.sh"
step() { python3 "$MYSQL_REGIONAL_DIR/lib/regional.py" "$1"; }
