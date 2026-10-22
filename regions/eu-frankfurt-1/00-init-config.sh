#!/usr/bin/env bash
# Create a private template only; never replace existing configuration.
set -euo pipefail
region_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$region_dir/../lib/init_config.py" "eu-frankfurt-1" "$@"
