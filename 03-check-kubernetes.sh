#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
need helm
connect_kubernetes
k version
k get nodes -o wide
check_nodes
k get storageclasses
resolve_storage_class
# Installing the operator creates cluster-scoped CRDs and RBAC.
for resource in customresourcedefinitions.apiextensions.k8s.io clusterroles.rbac.authorization.k8s.io clusterrolebindings.rbac.authorization.k8s.io namespaces; do
    [[ $(k auth can-i create "$resource") == yes ]] || die "Need permission to create $resource. Use a cluster administrator context for initial setup."
done
log 'Preflight passed. Worker image pulls, CSI provisioning, DNS, and inter-pod connectivity will be exercised during deployment.'
