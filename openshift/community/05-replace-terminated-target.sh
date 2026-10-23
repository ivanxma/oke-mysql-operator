#!/usr/bin/env bash
# Run on the existing jump host to reuse its image after terminating its target.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./05-replace-terminated-target.sh <new-private-IP> [--previous-instance-id <OCID>] [--apply] [--complete] [--allow-preflight-warnings]'
  echo 'Default: cloud-verified preview. --apply archives stale target state, registers the replacement, updates cluster DNS and waits for DNS verification.'
  echo 'Run on the existing jump host. Previous VM must be TERMINATED; replacement must use this workspace image in the selected compartment.'
  echo '--complete: after DNS, run preflight, installation monitoring and acceptance. Warnings stop unless explicitly acknowledged with --allow-preflight-warnings.'
  echo 'Previous instance is loaded from verified private state when available; supply its OCID only if no saved inventory exists.'
  exit 0
fi
[[ $# -ge 1 ]] || fail 'Supply the actual replacement private IP.'
address=$1 previous='' action=preview complete=false allow_warnings=false
shift
while [[ $# -gt 0 ]]; do
  case $1 in
    --apply) action=apply ;;
    --complete) complete=true ;;
    --allow-preflight-warnings) allow_warnings=true ;;
    --previous-instance-id) [[ $# -ge 2 ]] || fail 'Supply previous instance OCID.'; previous=$2; shift ;;
    *) fail 'Use --help for replacement arguments.' ;;
  esac
  shift
done
[[ -z $previous || $previous == ocid1.instance.* ]] || fail 'Supply the previous instance OCID.'
if $complete && [[ $action != apply ]]; then fail '--complete requires --apply.'; fi
echo 'PHASE 1/7: verify terminated/replacement VM identities and plan state cleanup.'
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
echo 'PHASE 2/7: register replacement address and refresh private SSH configuration.'
bash "$COMMUNITY_DIR/05-register-node-ip.sh" "$address" --assigned-to-target
bash "$COMMUNITY_DIR/01-prepare-host.sh" --ssh-only
echo 'PHASE 3/7: rediscover DNS scope, remove stale cluster A values and register the replacement.'
bash "$COMMUNITY_DIR/05-discover-dns-view.sh"
bash "$COMMUNITY_DIR/05-configure-dns.sh" --apply
echo 'PHASE 4/7: wait for DNS on admin and target; cached old answers may take time to expire.'
bash "$COMMUNITY_DIR/05-verify-dns.sh" --wait 600 --interval 15
if ! $complete; then
  echo 'Replacement registration and DNS verified. Installation acceptance NOT checked. Next: ./00-validate-environment.sh --existing-target, then ./05-wait-for-control-plane.sh and steps 06/11.'
  exit 0
fi
echo 'PHASE 5/7: validate required host/network prerequisites.'
preflight=0
bash "$COMMUNITY_DIR/00-validate-environment.sh" --existing-target || preflight=$?
if [[ $preflight -eq 2 ]] && $allow_warnings; then
  echo 'Preflight has no FAIL items. Continuing with explicitly acknowledged WARN items; external access remains unverified.'
elif [[ $preflight -ne 0 ]]; then
  echo 'STOPPED at preflight. Correct failures or review warnings; --allow-preflight-warnings acknowledges warnings only.' >&2
  exit "$preflight"
fi
echo 'PHASE 6/7: wait for installer completion. Reboots and revision rollouts can interrupt the API; progress is printed.'
monitor=0
bash "$COMMUNITY_DIR/05-wait-for-control-plane.sh" || monitor=$?
if [[ $monitor -ne 0 ]]; then
  echo 'Installation completion NOT confirmed. Collecting read-only diagnostics; no repair is automatic.' >&2
  bash "$COMMUNITY_DIR/05-diagnose-control-plane.sh" --node-bootstrap || true
  bash "$COMMUNITY_DIR/05-diagnose-control-plane.sh" || true
  exit "$monitor"
fi
echo 'PHASE 7/7: cluster acceptance and console HTTPS verification.'
bash "$COMMUNITY_DIR/06-check-control-plane.sh"
bash "$COMMUNITY_DIR/11-verify-web-access.sh"
echo 'COMPLETE: installer, cluster acceptance and console verification passed. External-access warnings remain as reported.'
