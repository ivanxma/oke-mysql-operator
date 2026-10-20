#!/usr/bin/env bash
set -euo pipefail
set +x
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)/lib/clusterset_common.sh"
export C1_CONTEXT="${C1_CONTEXT:-$KUBE_CONTEXT}"
export C1_SOURCE_NAMESPACE="${C1_SOURCE_NAMESPACE:-$PRIMARY_MYSQL_NAMESPACE}"
export C1_SOURCE_CLUSTER="${C1_SOURCE_CLUSTER:-$PRIMARY_MYSQL_CLUSTER_NAME}"
source "$SCRIPT_DIR/oke2/lib/common.sh"
if [[ -f "${MYSQLK8S_CLUSTER3_CONFIG:-$OKE2_DIR/cluster3.env}" ]]; then
    set -a
    source "${MYSQLK8S_CLUSTER3_CONFIG:-$OKE2_DIR/cluster3.env}"
    set +a
fi
export C1_KUBECONFIG="${C1_KUBECONFIG:-$HOME/.kube/config}"
export C2_KUBECONFIG C2_CONTEXT
export CLUSTER3_NAMESPACE="${CLUSTER3_NAMESPACE:-mysql-tertiary}"
export CLUSTER3_NAME="${CLUSTER3_NAME:-mycluster3}"
export CLUSTER3_SECRET="${CLUSTER3_SECRET:-mycluster3-root}"
export CLUSTER3_BASE_SERVER_ID="${CLUSTER3_BASE_SERVER_ID:-3000}"
export CLUSTER3_INSTANCES="${CLUSTER3_INSTANCES:-3}"
export CLUSTER3_ROUTERS="${CLUSTER3_ROUTERS:-2}"
export CLUSTER3_STORAGE_CLASS="${CLUSTER3_STORAGE_CLASS:-oci-bv}"
export CLUSTER3_STORAGE_SIZE="${CLUSTER3_STORAGE_SIZE:-10Gi}"
export C1_DNS_SERVERS="${C1_DNS_SERVERS:-}"
export C2_DNS_SERVERS="${C2_DNS_SERVERS:-}"
require_c2_config
if [[ -z "$C1_CONTEXT" ]]; then
    C1_CONTEXT="$(kubectl --kubeconfig "$C1_KUBECONFIG" config current-context)"
    export C1_CONTEXT
fi
