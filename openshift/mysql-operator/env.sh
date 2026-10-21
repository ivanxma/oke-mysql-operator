#!/usr/bin/env bash
# Source on the administration host; shared OKD env selects KUBECONFIG.
MYSQL_OPENSHIFT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MYSQL_REPO_ROOT=$(cd -- "$MYSQL_OPENSHIFT_DIR/../.." && pwd)
source "$MYSQL_OPENSHIFT_DIR/../community/env.sh"
export MYSQLK8S_CONFIG=${MYSQL_OPENSHIFT_CONFIG:-$MYSQL_REPO_ROOT/internal_doc/openshift/mysql-operator/config.env}
export MYSQLK8S_CLUSTERSET_CONFIG=$MYSQLK8S_CONFIG
if [[ -f $MYSQLK8S_CONFIG ]]; then
  set -a
  source "$MYSQLK8S_CONFIG"
  set +a
fi
export MYSQL_WORKER_NAMES=${MYSQL_WORKER_NAMES:-'worker01 worker02 worker03'}
export MYSQL_STATE_DIR=${MYSQL_STATE_DIR:-$MYSQL_REPO_ROOT/internal_doc/openshift/mysql-operator}
export MYSQLK8S_GENERATED_DIR=$MYSQL_STATE_DIR/generated
if [[ -z ${MYSQL_EXPECTED_API_SERVER:-} && -r $ADMIN_WORKDIR/cluster-domain ]]; then
  MYSQL_EXPECTED_API_SERVER="https://api.$(cat "$ADMIN_WORKDIR/cluster-domain"):6443"
fi
if [[ -z ${MYSQL_EXPECTED_API_SERVER:-} && -n ${CLUSTER_NAME:-} && -n ${BASE_DOMAIN:-} ]]; then
  MYSQL_EXPECTED_API_SERVER="https://api.$CLUSTER_NAME.$BASE_DOMAIN:6443"
fi
export MYSQL_EXPECTED_API_SERVER=${MYSQL_EXPECTED_API_SERVER:-}
