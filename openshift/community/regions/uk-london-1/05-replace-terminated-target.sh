#!/usr/bin/env bash
set -euo pipefail
region_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  exec bash "$region_dir/../../05-replace-terminated-target.sh" "$@"
fi
source "$region_dir/env.sh" ""
exec bash "$region_dir/../../05-replace-terminated-target.sh" "$@"
