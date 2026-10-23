#!/usr/bin/env bash
# Network probes only; saves a private result, changes no host or OCI configuration.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $# -eq 0 ]] || { echo 'Usage: 03-verify-gateway-paths.sh' >&2; exit 1; }
interpreter=python3
if [[ -x $ADMIN_WORKDIR/oci-sdk/bin/python ]]; then interpreter=$ADMIN_WORKDIR/oci-sdk/bin/python; fi
exec "$interpreter" "$SUBMARINER_DIR/lib/verify-gateway-paths.py"
