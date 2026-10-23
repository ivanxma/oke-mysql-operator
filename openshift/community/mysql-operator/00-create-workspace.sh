#!/usr/bin/env bash
# Generate private profiles on the jump host from the existing regional OKD profiles.
set -euo pipefail
set +x
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$dir/lib/create-workspace.py" "$@"
