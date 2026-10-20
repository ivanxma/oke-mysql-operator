#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
log 'Enter the MySQL root password when prompted. Try: SELECT @@hostname, @@version, @@read_only;'
# The operator sidecar includes MySQL Shell. Connect through Router, not directly to pod 0.
k --request-timeout=0 -n "$MYSQL_NAMESPACE" exec -it "$MYSQL_CLUSTER_NAME-0" -c sidecar -- \
    mysqlsh --sql --host "$MYSQL_CLUSTER_NAME.$MYSQL_NAMESPACE.svc" --port 3306 --user root --password
