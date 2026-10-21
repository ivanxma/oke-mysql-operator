#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./02-label-worker-nodes.sh'
  echo 'Label configured MYSQL_WORKER_NAMES (default: worker01 worker02 worker03).'
  echo 'Requires the shared environment and configured KUBECONFIG on the admin host.'
  echo 'Refuses control-plane, missing, non-Ready, cordoned or differently labelled workers.'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Configure MYSQL_WORKER_NAMES privately, not as command arguments.' >&2; exit 1; }
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/env.sh"
python3 "$MYSQL_OPENSHIFT_DIR/lib/label_workers.py"
