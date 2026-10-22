#!/usr/bin/env bash
set -euo pipefail
set +x
SUBMARINER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Use the numbered steps in README.md. London-only by default; no OKE or CNI replacement.\n'
  exit 0
fi
source "$SUBMARINER_DIR/env.sh"
command -v python3 >/dev/null || { printf 'Python 3 is required.\n' >&2; exit 1; }
run_step() { python3 "$SUBMARINER_DIR/lib/workflow.py" "$1"; }
