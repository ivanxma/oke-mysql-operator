#!/usr/bin/env bash
# Verify one manually created worker; approve only its matching kubelet CSRs.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./13-register-worker.sh <worker-private-IP> [--approve] [--wait <seconds>] [--refresh-host-key]'
  echo 'Default: read-only VM/SSH/CSR preview. --approve signs matching CSRs, waits for Ready and records the expected node count.'
  echo 'Optional host-key refresh requires --approve and revalidates VM ownership first.'
  exit 0
fi
[[ $# -ge 1 ]] || fail 'Supply the assigned worker IP.'
load_config; load_host; validate_cluster
[[ $ADMIN_HOST == 127.0.0.1 ]] || fail 'Run on the jump host from its generated execution folder.'
interpreter=python3
if [[ -x $ADMIN_WORKDIR/oci-sdk/bin/python ]]; then interpreter=$ADMIN_WORKDIR/oci-sdk/bin/python; fi
exec "$interpreter" "$COMMUNITY_DIR/lib/register-worker.py" "$@"
