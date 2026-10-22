#!/usr/bin/env bash
# Read-only discovery after manual worker VM provisioning; no worker IP inputs.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./12-discover-worker-nodes.sh (list nodes and pending CSRs; no approvals)'
  exit 0
fi
[[ $# -eq 0 ]] || fail 'No worker addresses are required. Use the selected cluster environment.'
load_config; load_host
remote_run "$ADMIN_WORKDIR" "$KUBECONFIG" <<'REMOTE'
set -euo pipefail
source "$1/env.sh"
export KUBECONFIG=$2
[[ -r $KUBECONFIG ]] || { echo 'The selected installation kubeconfig is unavailable.' >&2; exit 1; }
expected="https://api.$(cat "$ADMIN_WORKDIR/cluster-domain"):6443"
actual=$(oc --kubeconfig "$KUBECONFIG" config view --minify -o jsonpath='{.clusters[0].cluster.server}')
[[ $actual == "$expected" ]] || { echo 'Selected API differs from this installation; refusing discovery.' >&2; exit 1; }
oc --kubeconfig "$KUBECONFIG" --request-timeout=30s get nodes -o wide
oc --kubeconfig "$KUBECONFIG" --request-timeout=30s get csr \
  -o custom-columns='NAME:.metadata.name,SIGNER:.spec.signerName,REQUESTOR:.spec.username,CONDITIONS:.status.conditions[*].type'
printf '\nRead-only discovery complete. No VM created, CSR approved, node labeled or node deleted.\n'
printf 'Verify each pending CSR and its intended OCI instance/node before individual approval.\n'
REMOTE
