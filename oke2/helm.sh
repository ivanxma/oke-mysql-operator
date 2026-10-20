#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
need helm
require_c2_config
for argument in "$@"; do
    case "$argument" in --kubeconfig|--kubeconfig=*|--kube-context|--kube-context=*) die 'This wrapper fixes kubeconfig=config2 and context=c2.' ;; esac
done
export KUBECONFIG="$C2_KUBECONFIG"
exec helm --kubeconfig "$C2_KUBECONFIG" --kube-context "$C2_CONTEXT" "$@"
