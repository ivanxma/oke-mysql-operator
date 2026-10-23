#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --record-reviewed-prerequisites && $# -eq 1 ]]; then
  run_step review
else
  [[ $# -eq 0 ]] || { printf 'Use no arguments, or --record-reviewed-prerequisites after reviewing local network requirements.\n' >&2; exit 1; }
  run_step validate
fi
