#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $# -eq 0 ]] || { printf 'Use the private config; this step takes no arguments.\n' >&2; exit 1; }
run_step label
