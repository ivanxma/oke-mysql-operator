#!/usr/bin/env bash
# Run on the OL9 administration host after London installation acceptance.
_sub_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
_sub_root=$(cd -- "$_sub_dir/../../../.." && pwd)
source "$_sub_root/openshift/community/regions/uk-london-1/env.sh" "" || { return 1 2>/dev/null || exit 1; }
export SUBMARINER_CONFIG_FILE=${SUBMARINER_CONFIG_FILE:-$(dirname -- "$OKD_CONFIG_FILE")/submariner.env}
[[ -f $SUBMARINER_CONFIG_FILE ]] || { printf 'Create the private Submariner config from config.env.example first.\n' >&2; return 1 2>/dev/null || exit 1; }
set -a
if [[ -f ${SUBMARINER_CONFIG_FILE%.env}.optional.env ]]; then source "${SUBMARINER_CONFIG_FILE%.env}.optional.env"; fi
source "$SUBMARINER_CONFIG_FILE"
set +a
# Preserve an existing legacy deployment; new state belongs to the generated profile.
if [[ -z ${SUBMARINER_STATE_DIR:-} ]]; then
  SUBMARINER_STATE_DIR=$OKD_STATE_DIR/networking/submariner
  if [[ ! -e $SUBMARINER_STATE_DIR && -f $ADMIN_WORKDIR/internal_doc/networking/submariner/clusters.json ]]; then
    SUBMARINER_STATE_DIR=$ADMIN_WORKDIR/internal_doc/networking/submariner
  fi
fi
export SUBMARINER_STATE_DIR
export SUBMARINER_BIN="$SUBMARINER_STATE_DIR/bin/subctl"
export SUBMARINER_VERSION=${SUBMARINER_VERSION:-0.24.2}
export SUBMARINER_TARGET_API="https://api.$CLUSTER_NAME.$BASE_DOMAIN:6443"
export PATH="$ADMIN_WORKDIR/bin:$PATH"
unset _sub_dir _sub_root
