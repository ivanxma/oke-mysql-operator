#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
trap 'diagnostics' ERR
log "Waiting up to ${WAIT_TIMEOUT_SECONDS}s for all $MYSQL_INSTANCES MySQL instances to be ONLINE."
deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
while true; do
    status="$(k -n "$MYSQL_NAMESPACE" get innodbcluster "$MYSQL_CLUSTER_NAME" -o json)"
    state="$(printf '%s' "$status" | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get("status",{}).get("cluster",{}); print(c.get("status","PENDING"), c.get("onlineInstances",0)); assert d["spec"]["instances"] == int(sys.argv[1]), "Configured MYSQL_INSTANCES differs from deployed spec"' "$MYSQL_INSTANCES")"
    printf 'MySQL status / online instances: %s\n' "$state"
    [[ "$state" == "ONLINE $MYSQL_INSTANCES" ]] && break
    if (( SECONDS >= deadline )); then
        diagnostics
        die 'Timed out waiting for MySQL. Check events, PVCs, image pulls, and operator logs above.'
    fi
    remaining=$((deadline - SECONDS))
    if (( remaining > 10 )); then sleep 10; elif (( remaining > 0 )); then sleep "$remaining"; fi
done
k --request-timeout=0 -n "$MYSQL_NAMESPACE" rollout status statefulset/"$MYSQL_CLUSTER_NAME" --timeout="${WAIT_TIMEOUT_SECONDS}s"
# Router Deployment is created before the cluster becomes ONLINE.
k --request-timeout=0 -n "$MYSQL_NAMESPACE" rollout status deployment/"$MYSQL_CLUSTER_NAME-router" --timeout="${WAIT_TIMEOUT_SECONDS}s"
k -n "$MYSQL_NAMESPACE" get innodbcluster "$MYSQL_CLUSTER_NAME"
k -n "$MYSQL_NAMESPACE" get pods,pvc,services -l "mysql.oracle.com/cluster=$MYSQL_CLUSTER_NAME" -o wide
k -n "$MYSQL_NAMESPACE" get service "$MYSQL_CLUSTER_NAME"
log "Ready. Application endpoint: $MYSQL_CLUSTER_NAME.$MYSQL_NAMESPACE.svc:3306 (read/write) and :6447 (read-only)."
log 'Run step 07 for an interactive SQL check, or step 08 for a local port forward.'
