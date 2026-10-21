#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./12-port-forward-mysql.sh [--replica] (loopback only, keep running until Ctrl-C)'; exit 0; fi
replica=false
if [[ ${1:-} == --replica ]]; then replica=true; shift; fi
[[ $# == 0 ]] || exit 1
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
ns=$MYSQL_NAMESPACE cluster=$MYSQL_CLUSTER_NAME remote_port=3306
service=$cluster
if $replica; then ns=$REPLICA_MYSQL_NAMESPACE cluster=$REPLICA_MYSQL_CLUSTER_NAME service="$REPLICA_MYSQL_CLUSTER_NAME-instances"; fi
log "Connect on this admin host: mysql -h 127.0.0.1 -P $LOCAL_MYSQL_PORT -u root -p"
k --request-timeout=0 -n "$ns" port-forward --address=127.0.0.1 "service/$service" "$LOCAL_MYSQL_PORT:$remote_port"
