#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./01-prepare-admin.sh (OL9 admin packages and pinned Helm)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
bash "$MYSQL_REPO_ROOT/01-install-prerequisites.sh"
bash "$MYSQL_REPO_ROOT/02-install-helm.sh"
command -v oc >/dev/null || die 'Use the matching oc/kubectl tools prepared by openshift/community/02-download-okd.sh.'

if [[ -n ${MYSQL_OCI_COMPARTMENT_ID:-} ]] && ! oci_python >/dev/null; then
  as_root dnf install -y python3-pip
  umask 077
  mkdir -p "$MYSQL_STATE_DIR"
  python3 -m venv "$MYSQL_STATE_DIR/oci-sdk"
  "$MYSQL_STATE_DIR/oci-sdk/bin/python" -m pip install 'oci==2.187.0' 'PyYAML==6.0.3'
  oci_python >/dev/null || die 'OCI SDK/YAML preparation failed; review MYSQL_OCI_PYTHON if explicitly set.'
fi
