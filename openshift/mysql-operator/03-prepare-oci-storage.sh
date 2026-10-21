#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./03-prepare-oci-storage.sh [--prepare-overlay] (OCI CSI/device paths; optional dedicated Geneve NSG)'
  echo 'Set MYSQL_OCI_COMPARTMENT_ID, MYSQL_OCI_COMPARTMENT_NAME and MYSQL_OCI_REGION privately.'
  exit 0
fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
[[ $# -eq 0 || ( $# -eq 1 && $1 == --prepare-overlay ) ]] || die 'Supported option: --prepare-overlay'
export MYSQL_OCI_PREPARE_OVERLAY=false
[[ ${1:-} != --prepare-overlay ]] || export MYSQL_OCI_PREPARE_OVERLAY=true
connect_kubernetes
for variable in MYSQL_OCI_COMPARTMENT_ID MYSQL_OCI_COMPARTMENT_NAME MYSQL_OCI_REGION; do
  [[ -n ${!variable:-} ]] || die "Set $variable in private configuration."
done
interpreter=$(oci_python) || die 'Run step 01 to prepare OCI SDK/YAML, or select MYSQL_OCI_PYTHON privately.'
umask 077
"$interpreter" "$MYSQL_OPENSHIFT_DIR/lib/oci_storage.py"
