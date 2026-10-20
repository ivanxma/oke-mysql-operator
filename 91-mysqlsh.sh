#!/usr/bin/env bash
# Run MySQL Shell in a temporary Kubernetes pod with cluster DNS and networking.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/clusterset_common.sh"

usage() {
    cat <<'EOF'
Usage: ./91-mysqlsh.sh [options] [-- mysqlsh arguments]
  --namespace NAME  Database/client pod namespace (default: config.env).
  --cluster NAME    InnoDBCluster name (default: config.env).
  --replica         Use the replica namespace/cluster from clusterset.env.
  --member NUMBER   Connect directly to this database pod ordinal (default: 0).
  --user NAME       MySQL account (default: root); password is prompted.
  --no-connect      Start Shell without opening a database connection.
  --dry-run         Show the client pod manifest without creating it.
  -h, --help        Show this help.

Examples:
  ./91-mysqlsh.sh
  ./91-mysqlsh.sh --replica
  ./91-mysqlsh.sh --namespace mysql --cluster mycluster --member 1
  ./91-mysqlsh.sh --no-connect -- --py
EOF
}

target_namespace="$MYSQL_NAMESPACE"
target_cluster="$MYSQL_CLUSTER_NAME"
member=0
mysql_user=root
no_connect=false
dry_run=false
shell_args=()
while (( $# )); do
    case "$1" in
        --namespace|--cluster|--member|--user)
            (( $# >= 2 )) || die "Missing value for $1"
            case "$1" in
                --namespace) target_namespace="$2" ;;
                --cluster) target_cluster="$2" ;;
                --member) member="$2" ;;
                --user) mysql_user="$2" ;;
            esac
            shift 2 ;;
        --replica)
            target_namespace="$REPLICA_MYSQL_NAMESPACE"
            target_cluster="$REPLICA_MYSQL_CLUSTER_NAME"
            shift ;;
        --no-connect) no_connect=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; shell_args=("$@"); break ;;
        *) die "Unknown argument: $1 (see --help)" ;;
    esac
done
[[ "$target_namespace" =~ ^[a-z][a-z0-9-]*$ && ${#target_namespace} -le 63 ]] || die 'Invalid namespace.'
[[ "$target_cluster" =~ ^[a-z][a-z0-9-]*$ && ${#target_cluster} -le 40 ]] || die 'Invalid cluster name.'
[[ "$member" =~ ^[0-8]$ ]] || die 'Member must be a pod ordinal from 0 to 8.'
[[ -n "$mysql_user" ]] || die 'MySQL user must not be empty.'
[[ "$K8S_CLUSTER_DOMAIN" =~ ^[a-z0-9]+([.-][a-z0-9]+)*$ ]] || die 'Invalid K8S_CLUSTER_DOMAIN.'

connect_kubernetes
database_pod="$target_cluster-$member"
database_json="$(k -n "$target_namespace" get pod "$database_pod" -o json)"
client_pod="mysqlsh-$(date +%s)-$$-$RANDOM"
manifest="$(printf '%s' "$database_json" | python3 "$SCRIPT_DIR/lib/mysqlsh_pod.py" "$target_namespace" "$client_pod")"
target_host="$database_pod.$target_cluster-instances.$target_namespace.svc.$K8S_CLUSTER_DOMAIN"

log "Database members in namespace $target_namespace:"
k -n "$target_namespace" get pods -l "component=mysqld,mysql.oracle.com/cluster=$target_cluster" -o wide
log "Direct member endpoint: $target_host:3306"
log 'This connects directly to a database member. Its PRIMARY/SECONDARY role can change.'
if [[ "$dry_run" == true ]]; then
    printf '%s\n' "$manifest"
    exit 0
fi

created=false
cleanup() {
    local result=$?
    trap - EXIT
    if [[ "$created" == true ]]; then
        k -n "$target_namespace" delete pod "$client_pod" --ignore-not-found --wait=false >/dev/null || true
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s' "$manifest" | k -n "$target_namespace" create -f -
created=true
log "Waiting for temporary MySQL Shell pod: $target_namespace/$client_pod"
if ! k --request-timeout=0 -n "$target_namespace" wait --for=condition=Ready "pod/$client_pod" --timeout=180s; then
    k -n "$target_namespace" describe pod "$client_pod" >&2 || true
    die 'MySQL Shell pod failed to become ready.'
fi

connection_args=()
if [[ "$no_connect" == false ]]; then
    connection_args=(--host "$target_host" --port 3306 --user "$mysql_user" --password)
    log 'Enter the MySQL password when prompted. Use \quit to exit and remove the client pod.'
else
    log "Shell starts without a connection. Connect with: \connect $mysql_user@$target_host:3306"
fi
# Interactive terminals get a TTY; batch -e/--file commands also work over stdin.
exec_flags=(-i)
if [[ -t 0 && -t 1 ]]; then exec_flags=(-it); fi
k --request-timeout=0 -n "$target_namespace" exec "${exec_flags[@]}" "$client_pod" -c mysqlsh -- \
    mysqlsh "${connection_args[@]}" "${shell_args[@]}"
