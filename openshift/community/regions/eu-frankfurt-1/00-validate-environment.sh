#!/usr/bin/env bash
set -euo pipefail
region_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf "Usage: 00-validate-environment.sh (uses this region's private OKD configuration; see openshift/community/regions/README.md)\n"
  exit 0
fi
source "$region_dir/env.sh" ""
exec bash "$region_dir/../../00-validate-environment.sh" "$@"
