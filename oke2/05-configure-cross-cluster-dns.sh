#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/cluster3_common.sh"
python3 "$OKE2_DIR/lib/cluster3.py" dns "$@"
