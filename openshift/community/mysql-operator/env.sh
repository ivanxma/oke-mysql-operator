#!/usr/bin/env bash
# The generated launcher selects the existing OKD environment first.
set +x
MYSQL_REGIONAL_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MYSQL_REPO_ROOT=$(cd -- "$MYSQL_REGIONAL_DIR/../../.." && pwd)
: "${OKD_CONFIG_FILE:?Source a generated regional OKD 00-env.sh first}"
export MYSQL_REGIONAL_CONFIG=${MYSQL_REGIONAL_CONFIG:-$(dirname -- "$OKD_CONFIG_FILE")/mysql-operator/config.env}
[[ -f $MYSQL_REGIONAL_CONFIG ]] || { echo 'Generate the MySQL workspaces with step 00 first.' >&2; return 1 2>/dev/null || exit 1; }
set -a
source "$MYSQL_REGIONAL_CONFIG"
set +a
export MYSQL_STATE_DIR="$(dirname -- "$MYSQL_REGIONAL_CONFIG")/.state"
export MYSQLK8S_GENERATED_DIR="$MYSQL_STATE_DIR/generated"
export MYSQL_EXPECTED_API_SERVER="https://api.$CLUSTER_NAME.$BASE_DOMAIN:6443"
export MYSQL_OCI_COMPARTMENT_ID=$OCI_COMPARTMENT_ID MYSQL_OCI_COMPARTMENT_NAME=$OCI_COMPARTMENT_NAME MYSQL_OCI_REGION=$OCI_REGION
export MYSQL_OCI_PYTHON="$MYSQL_STATE_DIR/tools/bin/python"
export MYSQL_ROOT_PASSWORD_FILE="$(dirname -- "$MYSQL_REGIONAL_CONFIG")/.credentials/root-password"
export KUBE_CONTEXT=${KUBE_CONTEXT:-$($ADMIN_WORKDIR/bin/oc --kubeconfig "$KUBECONFIG" config current-context)}
export MYSQLK8S_CONFIG=$MYSQL_REGIONAL_CONFIG MYSQLK8S_CLUSTERSET_CONFIG=$MYSQL_REGIONAL_CONFIG
export PATH="$(dirname -- "$MYSQL_REGIONAL_CONFIG")/bin:$ADMIN_WORKDIR/bin:$PATH"
