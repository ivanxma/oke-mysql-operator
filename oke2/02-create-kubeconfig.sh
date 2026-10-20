#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
need oci
need kubectl
validate_destination
[[ "$C2_CLUSTER_ID" == ocid1.cluster.* ]] || die 'Set C2_CLUSTER_ID in oke2/config.env.'
case "$C2_KUBE_ENDPOINT" in PRIVATE_ENDPOINT|PUBLIC_ENDPOINT|VCN_HOSTNAME) ;; *) die 'Select PRIVATE_ENDPOINT, PUBLIC_ENDPOINT, or VCN_HOSTNAME.' ;; esac
state="$(oci2 ce cluster get --cluster-id "$C2_CLUSTER_ID" --query 'data."lifecycle-state"' --raw-output)"
[[ "$state" == ACTIVE ]] || die "Second OKE cluster is $state. Retry after it becomes ACTIVE. No kubeconfig was changed."

umask 077
destination_dir="$(dirname -- "$C2_KUBECONFIG")"
if [[ ! -d "$destination_dir" ]]; then mkdir -p -m 700 "$destination_dir"; fi
work_dir="$(mktemp -d "$destination_dir/.oke2-config.XXXXXX")"
trap 'rm -rf -- "$work_dir"' EXIT
stage="$work_dir/config2"

# Always generate into a fresh, separate file; never merge into the primary file.
oci2 ce cluster create-kubeconfig --cluster-id "$C2_CLUSTER_ID" --file "$stage" \
    --token-version 2.0.0 --kube-endpoint "$C2_KUBE_ENDPOINT" --with-auth-context
chmod 600 "$stage"
old_context="$(kubectl --kubeconfig "$stage" config current-context)"
[[ -n "$old_context" ]] || die 'OCI CLI generated no current context.'
if [[ "$old_context" != "$C2_CONTEXT" ]]; then
    kubectl --kubeconfig "$stage" config rename-context "$old_context" "$C2_CONTEXT"
fi
kubectl --kubeconfig "$stage" config use-context "$C2_CONTEXT" >/dev/null
# Check generated identity without exposing its certificate or exec credentials.
kubectl --kubeconfig "$stage" config view -o json | \
    python3 "$OKE2_DIR/lib/check_identity.py" "$C2_CLUSTER_ID" "$C2_CONTEXT"

if [[ -e "$C2_KUBECONFIG" ]]; then
    # Refresh only a config2 that already belongs to this same cluster/context.
    kubectl --kubeconfig "$C2_KUBECONFIG" config view -o json | \
        python3 "$OKE2_DIR/lib/check_identity.py" "$C2_CLUSTER_ID" "$C2_CONTEXT"
    backup="$C2_KUBECONFIG.backup.$(date -u +%Y%m%dT%H%M%SZ).$$"
    cp -p -- "$C2_KUBECONFIG" "$backup"
    chmod 600 "$backup"
    log "Existing config2 backed up: $backup"
fi
mv -f -- "$stage" "$C2_KUBECONFIG"
log "Second cluster configured: $C2_KUBECONFIG | context: $C2_CONTEXT"
log 'Run ./03-check-kubernetes.sh. Accessing a private endpoint requires a route to its VCN.'
