#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
require_c2_config
log "Second OKE cluster | kubeconfig: $C2_KUBECONFIG | context: $C2_CONTEXT"
k2 get --raw=/readyz
printf '\n'
k2 version
k2 get nodes -o wide
k2 get storageclasses
k2 get pods -n kube-system -o wide
log 'API connectivity checked. Review worker readiness and system pods before deploying workloads.'
