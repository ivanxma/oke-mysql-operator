#!/usr/bin/env bash
# Dedicated gateway NSGs only; preview by default.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
action=preview
if [[ ${1:-} == --apply && $# -eq 1 ]]; then action=apply
elif [[ $# -ne 0 ]]; then printf 'Usage: 03-prepare-gateway-network.sh [--apply]\n' >&2; exit 1; fi
interpreter=python3
if [[ -x $ADMIN_WORKDIR/oci-sdk/bin/python ]]; then interpreter=$ADMIN_WORKDIR/oci-sdk/bin/python; fi
exec "$interpreter" "$SUBMARINER_DIR/lib/gateway-network.py" "$action"
