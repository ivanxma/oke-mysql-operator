#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
require_c2_config
for argument in "$@"; do
    case "$argument" in --kubeconfig|--kubeconfig=*|--context|--context=*) die 'This wrapper fixes kubeconfig=config2 and context=c2.' ;; esac
done
export KUBECONFIG="$C2_KUBECONFIG"
exec kubectl --kubeconfig "$C2_KUBECONFIG" --context "$C2_CONTEXT" "$@"
