#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then echo 'Usage: ./05-wait-for-control-plane.sh (after manually launching the new OCI CoreOS VM)'; exit 0; fi
load_config; load_host
remote_run "$ADMIN_WORKDIR" "$KUBECONFIG" <<'REMOTE'
set -euo pipefail
source "$1/env.sh"
export KUBECONFIG=$2
[[ -r "$KUBECONFIG" ]] || { echo "Configured KUBECONFIG is unavailable: $KUBECONFIG" >&2; exit 1; }
# The installer wait flow uses the auth generated in its own install directory.
# Reject a different kubeconfig here instead of silently waiting on another cluster.
[[ "$KUBECONFIG" -ef "$1/sno/auth/kubeconfig" ]] || {
  echo 'For installer monitoring, KUBECONFIG must resolve to this installation auth file.' >&2; exit 1;
}
# Installer output includes the bootstrap password on success. Keep it in the
# protected auth file rather than terminal transcripts; pipefail preserves errors.
openshift-install --dir "$1/sno" wait-for install-complete --log-level=info 2>&1 | \
  sed -E 's/(password: ).*/\1[read protected auth file]/'
REMOTE
