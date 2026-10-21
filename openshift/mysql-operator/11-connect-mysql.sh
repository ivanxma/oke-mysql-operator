#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./11-connect-mysql.sh [--replica] (interactive SQL; password prompt)'; exit 0; fi
replica=false
if [[ ${1:-} == --replica ]]; then replica=true; shift; fi
[[ $# == 0 ]] || exit 1
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
ns=$MYSQL_NAMESPACE cluster=$MYSQL_CLUSTER_NAME port=3306
host="$cluster.$ns.svc.$K8S_CLUSTER_DOMAIN"
if $replica; then ns=$REPLICA_MYSQL_NAMESPACE cluster=$REPLICA_MYSQL_CLUSTER_NAME host=127.0.0.1; fi
k --request-timeout=0 -n "$ns" exec -it "$cluster-0" -c sidecar -- mysqlsh --sql \
  --host "$host" --port "$port" --user root --password
