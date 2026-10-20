#!/usr/bin/env bash
# Shared by the numbered scripts. Do not execute directly.
set -euo pipefail
# Never trace credentials, even if invoked with bash -x.
set +x
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${MYSQLK8S_CONFIG:-$SCRIPT_DIR/config.env}"
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
fi
export KUBE_CONTEXT="${KUBE_CONTEXT:-}"
export OPERATOR_NAMESPACE="${OPERATOR_NAMESPACE:-mysql-operator}"
export MYSQL_NAMESPACE="${MYSQL_NAMESPACE:-mysql}"
export MYSQL_CLUSTER_NAME="${MYSQL_CLUSTER_NAME:-mycluster}"
export MYSQL_SECRET_NAME="${MYSQL_SECRET_NAME:-mycluster-root}"
export HELM_VERSION="${HELM_VERSION:-v4.3.1}"
export OPERATOR_CHART_VERSION="${OPERATOR_CHART_VERSION:-2.3.0}"
export MYSQL_VERSION="${MYSQL_VERSION:-26.7.0}"
export MYSQL_INSTANCES="${MYSQL_INSTANCES:-3}"
export MYSQL_ROUTER_INSTANCES="${MYSQL_ROUTER_INSTANCES:-2}"
export STORAGE_CLASS="${STORAGE_CLASS:-}"
export MYSQL_STORAGE_SIZE="${MYSQL_STORAGE_SIZE:-10Gi}"
export MYSQL_ANTI_AFFINITY="${MYSQL_ANTI_AFFINITY:-required}"
export WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-1800}"
export LOCAL_MYSQL_PORT="${LOCAL_MYSQL_PORT:-3306}"
export MYSQL_ROOT_PASSWORD_FILE="${MYSQL_ROOT_PASSWORD_FILE:-}"

log() { printf '\n%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
as_root() {
    if [[ $EUID -eq 0 ]]; then "$@"; else need sudo; sudo "$@"; fi
}
validate_config() { need python3; python3 "$SCRIPT_DIR/lib/cluster_config.py" validate; }
connect_kubernetes() {
    need kubectl
    validate_config
    if [[ -z "$KUBE_CONTEXT" ]]; then
        KUBE_CONTEXT="$(kubectl config current-context)"
    fi
    [[ -n "$KUBE_CONTEXT" ]] || die 'No kubectl context configured.'
    export KUBE_CONTEXT
    log "Kubernetes context: $KUBE_CONTEXT | MySQL: $MYSQL_NAMESPACE/$MYSQL_CLUSTER_NAME"
    k get --raw=/readyz >/dev/null
}
k() { kubectl --context "$KUBE_CONTEXT" --request-timeout=30s "$@"; }
h() { helm --kube-context "$KUBE_CONTEXT" "$@"; }
resolve_storage_class() {
    local classes
    classes="$(k get storageclasses -o json)"
    STORAGE_CLASS="$(printf '%s' "$classes" | python3 "$SCRIPT_DIR/lib/cluster_config.py" storage)"
    export STORAGE_CLASS
    log "StorageClass: $STORAGE_CLASS; requested per MySQL instance: $MYSQL_STORAGE_SIZE"
}
check_nodes() {
    k get nodes -o json | python3 "$SCRIPT_DIR/lib/cluster_config.py" nodes
}
ensure_namespace() {
    # A read first avoids requiring update rights on an existing namespace.
    if [[ -z "$(k get namespace "$1" --ignore-not-found -o name)" ]]; then
        k create namespace "$1"
    fi
}
diagnostics() {
    k -n "$MYSQL_NAMESPACE" get innodbclusters,pods,pvc,services -o wide || true
    k -n "$MYSQL_NAMESPACE" get events --sort-by=.lastTimestamp | tail -35 || true
    printf 'Operator logs: kubectl --context %q -n %q logs deployment/mysql-operator --tail=100\n' \
        "$KUBE_CONTEXT" "$OPERATOR_NAMESPACE" >&2
}
