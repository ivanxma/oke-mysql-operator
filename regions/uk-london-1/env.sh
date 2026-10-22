#!/usr/bin/env bash
# Source to select this OKD site; optional first argument is a private config file.
_region_previous=${OKD_REGION:-}
export OKD_REGION=uk-london-1
if source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/lib/env.sh" "$@"; then
  unset _region_previous
else
  if [[ -n $_region_previous ]]; then export OKD_REGION=$_region_previous; else unset OKD_REGION; fi
  unset _region_previous
  return 1 2>/dev/null || exit 1
fi
