#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
k get crd innodbclusters.mysql.oracle.com >/dev/null
k --request-timeout=0 -n "$OPERATOR_NAMESPACE" rollout status deployment/mysql-operator --timeout=120s
check_nodes
resolve_storage_class
ensure_namespace "$MYSQL_NAMESPACE"
umask 077
mkdir -p "$SCRIPT_DIR/generated"
manifest="$SCRIPT_DIR/generated/innodbcluster.json"
python3 "$SCRIPT_DIR/lib/cluster_config.py" manifest > "$manifest"
# Validate the CR against the installed CRD before creating credentials or the cluster.
k -n "$MYSQL_NAMESPACE" apply --dry-run=server -f "$manifest" >/dev/null
existing_cluster="$(k -n "$MYSQL_NAMESPACE" get innodbcluster "$MYSQL_CLUSTER_NAME" --ignore-not-found -o name)"
existing_secret="$(k -n "$MYSQL_NAMESPACE" get secret "$MYSQL_SECRET_NAME" --ignore-not-found -o name)"
if [[ -n "$existing_secret" ]]; then
    # Read only key names, never print existing credentials.
    k -n "$MYSQL_NAMESPACE" get secret "$MYSQL_SECRET_NAME" -o json | python3 -c '
import base64,json,sys
d=json.load(sys.stdin).get("data", {})
if any(not d.get(k) for k in ("rootUser", "rootHost", "rootPassword")):
    sys.exit("ERROR: Existing Secret must contain nonempty rootUser, rootHost, rootPassword.")
if base64.b64decode(d["rootUser"]) != b"root" or base64.b64decode(d["rootHost"]) != b"%":
    sys.exit("ERROR: Existing Secret must use rootUser=root and rootHost=% for these scripts.")'
    log "Reusing Secret $MYSQL_SECRET_NAME; its password is unchanged."
elif [[ -n "$existing_cluster" ]]; then
    die 'The cluster exists but its configured Secret is missing. Restore the original Secret; do not generate new credentials.'
else
    work_dir="$(mktemp -d)"
    trap 'rm -rf -- "$work_dir"' EXIT
    if [[ -n "$MYSQL_ROOT_PASSWORD_FILE" ]]; then
        [[ -r "$MYSQL_ROOT_PASSWORD_FILE" ]] || die "Cannot read MYSQL_ROOT_PASSWORD_FILE."
        root_password="$(cat -- "$MYSQL_ROOT_PASSWORD_FILE")"
    else
        [[ -t 0 ]] || die 'No terminal. Set MYSQL_ROOT_PASSWORD_FILE to a protected password file.'
        read -r -s -p 'New MySQL root password (at least 12 characters): ' root_password
        printf '\n'
        read -r -s -p 'Confirm MySQL root password: ' confirmation
        printf '\n'
        [[ "$root_password" == "$confirmation" ]] || die 'Passwords do not match.'
        unset confirmation
    fi
    [[ ${#root_password} -ge 12 ]] || die 'Use a password of at least 12 characters.'
    # Keep passwords off process arguments, Helm release values, and generated manifests.
    printf '%s' root > "$work_dir/rootUser"
    printf '%s' '%' > "$work_dir/rootHost"
    printf '%s' "$root_password" > "$work_dir/rootPassword"
    unset root_password
    k -n "$MYSQL_NAMESPACE" create secret generic "$MYSQL_SECRET_NAME" \
        --from-file="rootUser=$work_dir/rootUser" --from-file="rootHost=$work_dir/rootHost" \
        --from-file="rootPassword=$work_dir/rootPassword"
fi
k -n "$MYSQL_NAMESPACE" apply -f "$manifest"
log "Cluster submitted. Manifest: $manifest. Run 06-verify-mysql-cluster.sh to wait for readiness."
