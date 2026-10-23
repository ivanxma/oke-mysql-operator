#!/usr/bin/env bash
# Publish only the two selected cluster zones in their existing regional views.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-sync-regional-dns.sh --peer-region <region> [--apply]'
  echo 'Default: cloud-verified preview. Apply copies the selected clusters three A sets into both existing private views.'
  echo 'Both generated profiles must have assigned VM IPs. Shared resolvers, forwarding rules and routes are unchanged.'
  exit 0
fi
peer='' action=preview
while [[ $# -gt 0 ]]; do
  case $1 in
    --peer-region) [[ $# -ge 2 ]] || fail 'Supply peer region.'; peer=$2; shift 2 ;;
    --apply) action=apply; shift ;;
    *) fail 'Use --help.' ;;
  esac
done
[[ $peer == uk-london-1 || $peer == eu-frankfurt-1 ]] || fail 'Select a supported peer region.'
load_config; validate_cluster
[[ $ADMIN_HOST == 127.0.0.1 && $peer != "$OCI_REGION" ]] || fail 'Run on the jump host and select the other region.'
interpreter=python3
if [[ -x $ADMIN_WORKDIR/oci-sdk/bin/python ]]; then interpreter=$ADMIN_WORKDIR/oci-sdk/bin/python; fi
exec "$interpreter" "$COMMUNITY_DIR/lib/sync-regional-dns.py" "$OKD_CONFIG_FILE" "$peer" "$action"
