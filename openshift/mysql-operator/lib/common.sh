#!/usr/bin/env bash
set -euo pipefail
set +x
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/env.sh"
source "$MYSQL_REPO_ROOT/lib/clusterset_common.sh"
connect_kubernetes() {
  need oc; need kubectl; need python3
  validate_config
  [[ -n ${KUBECONFIG:-} && -r $KUBECONFIG && $KUBECONFIG == /* && $KUBECONFIG != *:* ]] || die 'Run on the admin host after sourcing its env.sh; the selected absolute KUBECONFIG must exist.'
  [[ -n $MYSQL_EXPECTED_API_SERVER ]] || die 'Set MYSQL_EXPECTED_API_SERVER privately or retain ADMIN_WORKDIR/cluster-domain.'
  KUBE_CONTEXT=${KUBE_CONTEXT:-$(oc --kubeconfig "$KUBECONFIG" config current-context)}
  export KUBE_CONTEXT
  local server
  server=$(oc --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" config view --minify -o jsonpath='{.clusters[0].cluster.server}')
  [[ $server == "$MYSQL_EXPECTED_API_SERVER" ]] || die 'Selected API does not match this OKD deployment; refusing operation.'
  k get --raw=/readyz >/dev/null
  k get infrastructure.config.openshift.io cluster >/dev/null
  python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" validate
}
k() { oc --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" --request-timeout=30s "$@"; }
h() { helm --kubeconfig "$KUBECONFIG" --kube-context "$KUBE_CONTEXT" "$@"; }
worker_check() { k get nodes -o json | python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" workers; }

oci_python() {
  local candidate
  if [[ -n ${MYSQL_OCI_PYTHON:-} ]]; then
    "$MYSQL_OCI_PYTHON" -c 'import oci,yaml' >/dev/null 2>&1 || return 1
    printf '%s\n' "$MYSQL_OCI_PYTHON"
    return
  fi
  for candidate in python3 "$ADMIN_WORKDIR/oci-sdk/bin/python" "$MYSQL_STATE_DIR/oci-sdk/bin/python"; do
    if "$candidate" -c 'import oci,yaml' >/dev/null 2>&1; then printf '%s\n' "$candidate"; return; fi
  done
  return 1
}
