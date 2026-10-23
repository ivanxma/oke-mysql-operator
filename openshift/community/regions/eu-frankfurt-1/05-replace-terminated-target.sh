#!/usr/bin/env bash
set -euo pipefail
region_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$region_dir/env.sh" ""
exec bash "$region_dir/../../05-replace-terminated-target.sh" "$@"
