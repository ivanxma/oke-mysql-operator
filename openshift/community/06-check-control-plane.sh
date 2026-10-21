#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then echo 'Usage: ./06-check-control-plane.sh'; exit 0; fi
load_config; load_host
expected_nodes=${EXPECTED_NODE_COUNT:-1}
[[ $expected_nodes =~ ^[1-9][0-9]*$ ]] || fail 'EXPECTED_NODE_COUNT must be a positive integer.'
remote_run "$ADMIN_WORKDIR" "$KUBECONFIG" "$expected_nodes" <<'REMOTE'
set -euo pipefail
source "$1/env.sh"
export KUBECONFIG=$2
[[ -r "$KUBECONFIG" ]] || { echo "Configured KUBECONFIG is unavailable: $KUBECONFIG" >&2; exit 1; }
expected_server="https://api.$(cat "$ADMIN_WORKDIR/cluster-domain"):6443"
actual_server=$(oc config view --minify -o jsonpath='{.clusters[0].cluster.server}')
[[ $actual_server == "$expected_server" ]] || {
  echo "KUBECONFIG selects $actual_server; expected $expected_server. Stop and correct the environment." >&2; exit 1;
}
oc whoami
oc get nodes -o wide
oc get clusteroperators
oc get machineconfigpools
oc get storageclasses
oc -n openshift-ingress get pods -o wide
oc -n openshift-console get route console
oc get nodes -o json | jq -e --argjson n "$3" '.items | length == $n' >/dev/null
oc get nodes -o json | jq -e 'all(.items[]; any(.status.conditions[]; .type=="Ready" and .status=="True"))' >/dev/null
oc get clusteroperators -o json | jq -e 'all(.items[];
  any(.status.conditions[]; .type=="Available" and .status=="True") and
  any(.status.conditions[]; .type=="Degraded" and .status=="False") and
  any(.status.conditions[]; .type=="Progressing" and .status=="False"))' >/dev/null || {
  echo 'Some operators have not settled. Inspect their conditions; do not treat the cluster as ready.' >&2; exit 1;
}
oc get machineconfigpools -o json | jq -e 'all(.items[];
  any(.status.conditions[]; .type=="Updated" and .status=="True") and
  all(.status.conditions[]; .type!="Degraded" or .status=="False"))' >/dev/null
printf '\nReady: %s OKD node(s), healthy ClusterOperators and updated machine-config pools.\n' "$3"
printf 'Bootstrap password location on OL9 host: %s/sno/auth/kubeadmin-password\n' "$1"
REMOTE
