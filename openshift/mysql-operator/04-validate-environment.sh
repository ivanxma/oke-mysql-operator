#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./04-validate-environment.sh (read-only; requires labelled workers and persistent storage)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes
need helm
worker_check
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" network-check
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" capacity
resolve_storage_class
if [[ -n $REPLICA_STORAGE_CLASS ]]; then k get storageclass "$REPLICA_STORAGE_CLASS" >/dev/null; fi
for resource in namespaces securitycontextconstraints.security.openshift.io customresourcedefinitions.apiextensions.k8s.io clusterroles.rbac.authorization.k8s.io clusterrolebindings.rbac.authorization.k8s.io; do
  [[ $(k auth can-i create "$resource") == yes ]] || die "Initial installation needs permission to create $resource."
done
k get storageclasses
k get nodes -l workload=mysql-cluster -o wide
log 'Read-only checks passed. Verify CSI provisioning, fsGroup/SELinux, capacity and registry pulls during deployment.'
log 'This validation step is read-only. Step 03 prepares OCI CSI when that storage option is selected.'
