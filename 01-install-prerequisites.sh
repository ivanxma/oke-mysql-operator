#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $(uname -s) == Linux ]] || die 'Run this installer on Enterprise Linux 9.'
# shellcheck disable=SC1091
source /etc/os-release
[[ ${VERSION_ID%%.*} == 9 ]] || die "Expected Linux 9; found ${PRETTY_NAME:-unknown}."
case "$ID" in ol|rhel|rocky|almalinux|centos) ;; *) die "Unsupported Linux distribution: $ID" ;; esac
need dnf
log 'Installing local tools (sudo is used only for OS package/binary installation).'
as_root dnf install -y ca-certificates curl tar gzip python3 coreutils
validate_config
log 'Prerequisites ready. Step 02 installs Helm; step 03 checks kubectl and Kubernetes.'
