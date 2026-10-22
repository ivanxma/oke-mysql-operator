#!/usr/bin/env bash
# Modify only cluster DNS in an existing private zone in the selected compartment.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
action=plan
view_id=''
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-configure-dns.sh [--apply] [--view-id <private-view-OCID>]'
  echo 'Default is read-only. Finds one exact cluster private zone in selected compartment.'
  echo 'Automatically uses private view state saved by 05-discover-dns-view.sh.'
  echo 'For a missing zone, --view-id selects an existing compartment-owned view for creation.'
  echo 'View creation and resolver attachments remain DNS-owner prerequisites.'
  exit 0
fi
while [[ $# -gt 0 ]]; do
  case $1 in
    --apply) action=apply; shift ;;
    --view-id) [[ $# -ge 2 && $2 == ocid1.dnsview.* ]] || fail 'Supply a private view OCID.'; view_id=$2; shift 2 ;;
    *) fail 'Use --apply and/or --view-id <private-view-OCID>.' ;;
  esac
done
load_config; load_host; validate_cluster
remote_python "$COMMUNITY_DIR/lib/configure-dns.py" "$ADMIN_WORKDIR" "$OCI_COMPARTMENT_ID" \
  "$OCI_COMPARTMENT_NAME" "$OCI_REGION" "$CLUSTER_NAME.$BASE_DOMAIN" "$NODE_IP" "$action" "$view_id" "$MACHINE_CIDR"
