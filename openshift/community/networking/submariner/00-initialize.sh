#!/usr/bin/env bash
# Discover the selected London cluster and generate private networking launchers.
set -euo pipefail
sub_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./00-initialize.sh (after sourcing the generated London 00-env.sh)'
  echo 'Read-only cluster discovery; creates private config/launchers beside the selected OKD profile. No cluster or OCI changes.'
  exit 0
fi
[[ $# -eq 0 ]] || { echo 'Use --help; private settings come from the shared environment.' >&2; exit 1; }
source "$sub_dir/../../regions/uk-london-1/env.sh" "${OKD_CONFIG_FILE:-}"
export SUBMARINER_CONFIG_FILE=${SUBMARINER_CONFIG_FILE:-$(dirname -- "$OKD_CONFIG_FILE")/submariner.env}
# Preserve an existing legacy deployment; new state belongs to the generated profile.
if [[ -z ${SUBMARINER_STATE_DIR:-} ]]; then
  SUBMARINER_STATE_DIR=$OKD_STATE_DIR/networking/submariner
  if [[ ! -e $SUBMARINER_STATE_DIR && -f $ADMIN_WORKDIR/internal_doc/networking/submariner/clusters.json ]]; then
    SUBMARINER_STATE_DIR=$ADMIN_WORKDIR/internal_doc/networking/submariner
  fi
fi
export SUBMARINER_STATE_DIR
export SUBMARINER_TARGET_API="https://api.$CLUSTER_NAME.$BASE_DOMAIN:6443"
export SUBMARINER_VERSION=${SUBMARINER_VERSION:-0.24.2}
exec python3 "$sub_dir/lib/workflow.py" initialize
