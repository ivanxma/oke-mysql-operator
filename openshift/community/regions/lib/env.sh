#!/usr/bin/env bash
# Shared profile loader. Sourcing a profile performs no network operations.
_region_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd)
_region_load() (
  set +x
  local region=$1 config=$2 variable
  local keys='ADMIN_HOST ADMIN_USER ADMIN_WORKDIR TARGET_USER NODE_IP SSH_IDENTITY OKD_VERSION CLUSTER_NAME BASE_DOMAIN MACHINE_CIDR POD_CIDR SERVICE_CIDR INSTALL_DISK WORKER_INSTALL_DISK EXPECTED_NODE_COUNT INGRESS_CA_FILE OCI_BUCKET OCI_NAMESPACE OCI_REGION OCI_AUTH OCI_COMPARTMENT_ID OCI_COMPARTMENT_NAME CLIENT_CIDR KUBECONFIG OKD_STATE_DIR SOCKS_PORT MYSQL_OPENSHIFT_CONFIG MYSQL_STATE_DIR MYSQL_EXPECTED_API_SERVER KUBE_CONTEXT MYSQL_OCI_PYTHON'
  # A profile is authoritative: inherited settings must never select the other site.
  for variable in $keys; do unset "$variable"; done
  [[ -f $config ]] || { printf 'Missing private regional config. Run this region\x27s 00-init-config.sh first.\n' >&2; return 1; }
  if [[ -f ${config%.env}.optional.env ]]; then
    source "${config%.env}.optional.env" || return 1
  fi
  # Primary settings win for backward compatibility with existing configurations.
  source "$config" || return 1
  [[ ${OCI_REGION:-} == "$region" ]] || { printf 'OCI_REGION must match the selected regional folder.\n' >&2; return 1; }
  [[ ${OCI_AUTH:-instance_principal} == instance_principal ]] || { printf 'Only instance-principal OCI authentication is allowed.\n' >&2; return 1; }
  [[ ${ADMIN_WORKDIR:-} == /home/* && $ADMIN_WORKDIR != *'/../'* && $ADMIN_WORKDIR != *$'\n'* ]] || {
    printf 'Set a dedicated absolute ADMIN_WORKDIR under /home/ in the private regional config.\n' >&2; return 1;
  }
  : "${ADMIN_USER:=opc}" "${TARGET_USER:=core}" "${OKD_VERSION:=5.0.0-okd-scos.3}"
  : "${INSTALL_DISK:=/dev/sda}" "${WORKER_INSTALL_DISK:=/dev/sda}" "${EXPECTED_NODE_COUNT:=1}"
  OCI_AUTH=instance_principal
  OKD_STATE_DIR=${OKD_STATE_DIR:-$(dirname -- "$config")/.state}
  if [[ -f $OKD_STATE_DIR/bucket.env ]]; then
    source "$OKD_STATE_DIR/bucket.env" || return 1
  fi
  if [[ -f $OKD_STATE_DIR/node-ip.env ]]; then
    source "$OKD_STATE_DIR/node-ip.env" || return 1
  fi
  if [[ -f $OKD_STATE_DIR/worker-nodes.env ]]; then
    source "$OKD_STATE_DIR/worker-nodes.env" || return 1
  fi
  KUBECONFIG=${KUBECONFIG:-$ADMIN_WORKDIR/sno/auth/kubeconfig}
  MYSQL_OPENSHIFT_CONFIG=${MYSQL_OPENSHIFT_CONFIG:-$ADMIN_WORKDIR/internal_doc/mysql-operator/config.env}
  MYSQL_STATE_DIR=${MYSQL_STATE_DIR:-$ADMIN_WORKDIR/internal_doc/mysql-operator}
  MYSQL_EXPECTED_API_SERVER="https://api.${CLUSTER_NAME:-}.${BASE_DOMAIN:-}:6443"
  # Context is selected from this explicit kubeconfig by the existing OKD adapter.
  for variable in $keys; do
    if [[ $variable == NODE_IP && -z ${NODE_IP:-} ]]; then printf 'unset NODE_IP\n'
    else printf 'export %s=%q\n' "$variable" "${!variable:-}"; fi
  done
  printf 'export OKD_REGION=%q OKD_SELECTED_REGION=%q OKD_CONFIG_FILE=%q\n' "$region" "$region" "$config"
)
# Changing regions without an explicit argument selects that region's default file.
_region_config=${1:-${OKD_CONFIG_FILE:-}}
if [[ -z $_region_config || ( ${OKD_SELECTED_REGION:-$OKD_REGION} != "$OKD_REGION" && -z ${1:-} ) ]]; then
  _region_config=$_region_root/generated/openshift/$OKD_REGION/community.env
  # Use the existing legacy default only when no generated profile exists.
  if [[ ! -f $_region_config && -f $_region_root/internal_doc/regions/$OKD_REGION/community.env ]]; then
    _region_config=$_region_root/internal_doc/regions/$OKD_REGION/community.env
  fi
fi
if _region_assignments=$(_region_load "$OKD_REGION" "$_region_config"); then
  eval "$_region_assignments"
  export PATH="$ADMIN_WORKDIR/bin:$PATH"
  unset _region_assignments _region_config _region_root
  unset -f _region_load
else
  unset _region_assignments _region_config _region_root
  unset -f _region_load
  return 1 2>/dev/null || exit 1
fi
