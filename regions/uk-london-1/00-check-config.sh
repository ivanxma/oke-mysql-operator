#!/usr/bin/env bash
# Local-only configuration and cross-site address/workspace checks. No SSH/OCI.
set -euo pipefail
region_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$region_dir/env.sh" ""
exec python3 "$region_dir/../lib/check_config.py" "$@"
