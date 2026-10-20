#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
need helm
connect_kubernetes
h repo add mysql-operator https://mysql.github.io/mysql-operator/ --force-update
h repo update mysql-operator
# Explicit status filters work with Helm 3 and 4; Helm 4 removed list --all.
releases="$(h list -n "$OPERATOR_NAMESPACE" \
    --deployed --failed --pending --uninstalled --superseded --uninstalling \
    --filter '^mysql-operator$' -o json)"
release_state="$(printf '%s' "$releases" | python3 -c 'import json,sys; r=json.load(sys.stdin); print((r[0]["chart"]+" "+r[0]["status"]) if r else "absent")')"
if [[ "$release_state" == absent ]]; then
    # A global operator is appropriate for a fresh cluster. Keep its topology fixed.
    h install mysql-operator mysql-operator/mysql-operator \
        --version "$OPERATOR_CHART_VERSION" --namespace "$OPERATOR_NAMESPACE" --create-namespace \
        --wait --timeout "${WAIT_TIMEOUT_SECONDS}s"
else
    [[ "$release_state" == "mysql-operator-$OPERATOR_CHART_VERSION deployed" ]] || \
        die "Existing release: $release_state. Resolve this release explicitly; this script does not upgrade or reinstall an operator."
    log "Reusing deployed operator chart $OPERATOR_CHART_VERSION."
fi
k wait --for=condition=Established crd/innodbclusters.mysql.oracle.com --timeout=120s
k --request-timeout=0 -n "$OPERATOR_NAMESPACE" rollout status deployment/mysql-operator --timeout="${WAIT_TIMEOUT_SECONDS}s"
log 'MySQL Operator ready. Continue with step 05.'
