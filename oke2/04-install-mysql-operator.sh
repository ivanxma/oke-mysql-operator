#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/cluster3_common.sh"
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --dry-run ) ]] || die 'Usage: ./04-install-mysql-operator.sh [--dry-run]'
python3 "$OKE2_DIR/lib/cluster3.py" identity
# Reuse the pinned Helm installer, isolated from the first cluster's config.env.
export MYSQLK8S_CONFIG=/dev/null KUBECONFIG="$C2_KUBECONFIG" KUBE_CONTEXT="$C2_CONTEXT"
export MYSQL_NAMESPACE="$CLUSTER3_NAMESPACE" MYSQL_CLUSTER_NAME="$CLUSTER3_NAME"
export MYSQL_SECRET_NAME="$CLUSTER3_SECRET"
if [[ "${1:-}" == --dry-run ]]; then
    printf 'Preview: install/reuse mysql-operator chart %s in context %s, kubeconfig %s, namespace %s.\n' \
        "$OPERATOR_CHART_VERSION" "$C2_CONTEXT" "$C2_KUBECONFIG" "$OPERATOR_NAMESPACE"
    exit 0
fi
bash "$SCRIPT_DIR/04-install-mysql-operator.sh"
printf '\nOperator installed in OKE c2. Continue with 05-configure-cross-cluster-dns.sh.\n'
