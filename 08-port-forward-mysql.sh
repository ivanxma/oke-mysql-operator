#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
log "In a second terminal on this machine: mysql -h 127.0.0.1 -P $LOCAL_MYSQL_PORT -u root -p"
log 'Forwarding on loopback only; keep this terminal open. Press Ctrl-C to stop.'
k --request-timeout=0 -n "$MYSQL_NAMESPACE" port-forward --address 127.0.0.1 \
    "service/$MYSQL_CLUSTER_NAME" "$LOCAL_MYSQL_PORT:mysql"
