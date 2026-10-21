#!/usr/bin/env bash
# Source this file to select the private deployment environment.
# Never fall back to ~/.kube/config. No deployment addresses or key paths belong here.
_okd_env_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export OKD_STATE_DIR=${OKD_STATE_DIR:-$_okd_env_dir/../../internal_doc/openshift/.state}
export OKD_CONFIG_FILE=${OKD_CONFIG_FILE:-$_okd_env_dir/../../internal_doc/openshift/community.env}
if [[ -f "$OKD_CONFIG_FILE" ]]; then
  source "$OKD_CONFIG_FILE"
fi
if [[ -z ${ADMIN_WORKDIR:-} ]]; then
  printf 'Set ADMIN_WORKDIR in the private file selected by OKD_CONFIG_FILE.\n' >&2
  unset _okd_env_dir
  return 1 2>/dev/null || exit 1
fi
export ADMIN_WORKDIR
export KUBECONFIG=${KUBECONFIG:-$ADMIN_WORKDIR/sno/auth/kubeconfig}
export PATH="$ADMIN_WORKDIR/bin:$PATH"
unset _okd_env_dir
