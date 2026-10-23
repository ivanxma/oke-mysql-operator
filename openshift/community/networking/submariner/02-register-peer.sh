#!/usr/bin/env bash
# Discover the peer from its generated regional profile; no cluster/OCI writes.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
region='' reviewed=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --peer-region) [[ $# -ge 2 ]] || exit 1; region=$2; shift 2 ;;
    --record-reviewed-network) reviewed=true; shift ;;
    *) printf 'Usage: 02-register-peer.sh --peer-region <region> [--record-reviewed-network]\n' >&2; exit 1 ;;
  esac
done
[[ -n $region ]] || { printf 'Supply --peer-region.\n' >&2; exit 1; }
python3 "$SUBMARINER_DIR/lib/workflow.py" register-peer "$region" "$reviewed"
