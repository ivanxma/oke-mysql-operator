#!/usr/bin/env bash
# Run on the existing jump host to reuse its image after terminating its target.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-replace-terminated-target.sh <new-private-IP> --previous-instance-id <OCID> [--apply]'
  echo 'Default: cloud-verified preview. --apply archives stale target state, registers the replacement, updates cluster DNS and waits for DNS verification.'
  echo 'Run on the existing jump host. Previous VM must be TERMINATED; replacement must use this workspace image in the selected compartment.'
  exit 0
fi
[[ $# -ge 3 && $2 == --previous-instance-id ]] || fail 'Supply new IP and --previous-instance-id <terminated-instance-OCID>.'
address=$1 previous=$3 action=preview
shift 3
if [[ ${1:-} == --apply ]]; then action=apply; shift; fi
[[ $# -eq 0 && $previous == ocid1.instance.* ]] || fail 'Use --help for replacement arguments.'
load_config preparation
[[ $ADMIN_HOST == 127.0.0.1 && -d $ADMIN_WORKDIR ]] || fail 'Run this script in the generated folder ON the existing jump host (ADMIN_HOST=127.0.0.1).'
interpreter=python3
if [[ -x $ADMIN_WORKDIR/oci-sdk/bin/python ]]; then interpreter=$ADMIN_WORKDIR/oci-sdk/bin/python; fi
"$interpreter" "$COMMUNITY_DIR/lib/replace-terminated-target.py" "$ADMIN_WORKDIR" "$STATE_DIR" \
  "$OCI_COMPARTMENT_ID" "$OCI_REGION" "$CLUSTER_NAME" "$BASE_DOMAIN" "$MACHINE_CIDR" "$address" "$previous" "$action"
if [[ $action != apply ]]; then
  echo 'No changes made. Repeat with --apply to archive stale state and re-register target/DNS.'
  exit 0
fi
bash "$COMMUNITY_DIR/05-register-node-ip.sh" "$address" --assigned-to-target
bash "$COMMUNITY_DIR/01-prepare-host.sh" --ssh-only
bash "$COMMUNITY_DIR/05-discover-dns-view.sh"
bash "$COMMUNITY_DIR/05-configure-dns.sh" --apply
bash "$COMMUNITY_DIR/05-verify-dns.sh" --wait 600 --interval 15
echo 'Replacement registration and DNS verified. Next: ./00-validate-environment.sh --existing-target, then ./05-wait-for-control-plane.sh and steps 06/11.'
