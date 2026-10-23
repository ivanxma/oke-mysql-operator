#!/usr/bin/env bash
# Source this file to select the private deployment environment.
# Never fall back to ~/.kube/config. No deployment addresses or key paths belong here.
_okd_env_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Regional profiles use the same installer with isolated configuration and state.
if [[ -n ${OKD_REGION:-} ]]; then
  case "$OKD_REGION" in uk-london-1|eu-frankfurt-1) ;; *) printf 'Unsupported OKD_REGION.\n' >&2; return 1 2>/dev/null || exit 1 ;; esac
  source "$_okd_env_dir/regions/$OKD_REGION/env.sh" "${OKD_CONFIG_FILE:-}" || { return 1 2>/dev/null || exit 1; }
  unset _okd_env_dir
  return 0 2>/dev/null || exit 0
fi
if [[ -z ${OKD_CONFIG_FILE:-} ]]; then
  OKD_CONFIG_FILE=$_okd_env_dir/../../generated/openshift/legacy/community.env
  if [[ ! -f $OKD_CONFIG_FILE && -f $_okd_env_dir/../../internal_doc/openshift/community.env ]]; then
    OKD_CONFIG_FILE=$_okd_env_dir/../../internal_doc/openshift/community.env
  fi
fi
export OKD_CONFIG_FILE
export OKD_STATE_DIR=${OKD_STATE_DIR:-$(dirname -- "$OKD_CONFIG_FILE")/.state}
if [[ -f ${OKD_CONFIG_FILE%.env}.optional.env ]]; then
  source "${OKD_CONFIG_FILE%.env}.optional.env" || { return 1 2>/dev/null || exit 1; }
fi
if [[ -f "$OKD_CONFIG_FILE" ]]; then
  source "$OKD_CONFIG_FILE"
fi
if [[ -z ${ADMIN_WORKDIR:-} ]]; then
  printf 'Set ADMIN_WORKDIR in the private file selected by OKD_CONFIG_FILE.\n' >&2
  unset _okd_env_dir
  return 1 2>/dev/null || exit 1
fi
if [[ -f $OKD_STATE_DIR/bucket.env ]]; then
  source "$OKD_STATE_DIR/bucket.env" || { return 1 2>/dev/null || exit 1; }
fi
if [[ -f $OKD_STATE_DIR/node-ip.env ]]; then
  source "$OKD_STATE_DIR/node-ip.env" || { return 1 2>/dev/null || exit 1; }
fi
if [[ -f $OKD_STATE_DIR/worker-nodes.env ]]; then
  source "$OKD_STATE_DIR/worker-nodes.env" || { return 1 2>/dev/null || exit 1; }
fi
export ADMIN_WORKDIR
export KUBECONFIG=${KUBECONFIG:-$ADMIN_WORKDIR/sno/auth/kubeconfig}
export PATH="$ADMIN_WORKDIR/bin:$PATH"
unset _okd_env_dir
