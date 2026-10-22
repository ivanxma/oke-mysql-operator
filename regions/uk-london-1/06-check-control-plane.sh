#!/usr/bin/env bash
set -euo pipefail
region_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf "Usage: 06-check-control-plane.sh (uses this region's private OKD configuration; see regions/README.md)\n"
  exit 0
fi
source "$region_dir/env.sh" ""
exec bash "$region_dir/../../openshift/community/06-check-control-plane.sh" "$@"
