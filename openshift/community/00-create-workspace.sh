#!/usr/bin/env bash
# Generate a private execution folder after cloning; no installation/cloud writes.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo 'Install Python 3 before starting.' >&2; exit 1; }
exec python3 "$script_dir/lib/create-workspace.py" "$@"
