#!/usr/bin/env bash
# Shared local SSH wrapper. No connections are made by sourcing this file.
set -euo pipefail
COMMUNITY_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR=${OKD_STATE_DIR:-$COMMUNITY_DIR/../../internal_doc/openshift/.state}
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Required command missing: $1"; }
load_config() {
  OKD_VERSION=${OKD_VERSION:-5.0.0-okd-scos.3}
  ADMIN_HOST=${ADMIN_HOST:-} ADMIN_USER=${ADMIN_USER:-opc} ADMIN_WORKDIR=${ADMIN_WORKDIR:-}
  CLUSTER_NAME=${CLUSTER_NAME:-okd-sno} BASE_DOMAIN=${BASE_DOMAIN:-} NODE_IP=${NODE_IP:-}
  MACHINE_CIDR=${MACHINE_CIDR:-} POD_CIDR=${POD_CIDR:-10.128.0.0/14} SERVICE_CIDR=${SERVICE_CIDR:-172.30.0.0/16}
  INSTALL_DISK=${INSTALL_DISK:-/dev/sda}
  OCI_BUCKET=${OCI_BUCKET:-} OCI_NAMESPACE=${OCI_NAMESPACE:-} OCI_REGION=${OCI_REGION:-} OCI_AUTH=${OCI_AUTH:-instance_principal}
  # The shared environment is the only kubeconfig-selection entry point.
  source "$COMMUNITY_DIR/env.sh"
  STATE_DIR=$OKD_STATE_DIR
  if [[ -n ${OKD_REGION:-} ]]; then
    need python3
    if [[ ${1:-} == preparation ]]; then
      python3 "$COMMUNITY_DIR/../../regions/lib/check_config.py" --preparation || fail 'Regional preparation checks failed; no remote command was run.'
    else
      python3 "$COMMUNITY_DIR/../../regions/lib/check_config.py" || fail 'Regional configuration checks failed; no remote command was run.'
    fi
  fi
  [[ $KUBECONFIG == /* && $KUBECONFIG != *:* ]] || fail 'KUBECONFIG must name one absolute path on the admin host.'
}
load_host() {
  [[ -f "$STATE_DIR/host.env" ]] || fail 'Run ./01-prepare-host.sh using the private deployment configuration first.'
  local expected_host=${ADMIN_HOST:-} expected_user=${ADMIN_USER:-} expected_target=${NODE_IP:-}
  source "$STATE_DIR/host.env"
  if [[ -n ${OKD_REGION:-} ]]; then
    [[ $SSH_HOST == "$expected_host" && $SSH_USER == "$expected_user" ]] ||
      fail 'Saved SSH state does not match the regional profile. Run its step 01 to prepare matching state.'
  fi
  validate_host "$SSH_USER" "$SSH_HOST"
}
validate_host() {
  [[ $1 =~ ^[a-z_][a-z0-9_-]*$ ]] || fail 'Invalid Linux account name.'
  need python3
  python3 - "$2" <<'PY'
import ipaddress, sys
try:
    ipaddress.IPv4Address(sys.argv[1])
except ValueError:
    raise SystemExit('Supply a literal IPv4 address for the OL9 OCI VM.')
PY
}
remote_run() {
  need ssh
  local cmd='bash -s --' arg
  for arg in "$@"; do printf -v cmd '%s %q' "$cmd" "$arg"; done
  ssh -F "$STATE_DIR/ssh_config" okd-admin "$cmd"
}
validate_cluster() {
  python3 - "$CLUSTER_NAME" "$BASE_DOMAIN" "${NODE_IP:-}" "$MACHINE_CIDR" "$POD_CIDR" "$SERVICE_CIDR" "$INSTALL_DISK" "$OKD_VERSION" "${1:-installation}" <<'PY'
import ipaddress, re, sys
name, domain, addr, machine, pod, service, disk, version, stage = sys.argv[1:]
label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
if not re.fullmatch(label, name): raise SystemExit('Invalid CLUSTER_NAME')
if not domain or not all(re.fullmatch(label, x) for x in domain.split('.')):
    raise SystemExit('Set BASE_DOMAIN in the private deployment configuration to your actual DNS domain')
if domain.endswith('example.com'): raise SystemExit('Replace the example DNS domain')
try:
    node = ipaddress.IPv4Address(addr) if addr or stage != 'preparation' else None
    nets = [ipaddress.IPv4Network(x) for x in (machine, pod, service)]
except ValueError as e: raise SystemExit(str(e))
if node is not None and (node not in nets[0] or node in (nets[0].network_address, nets[0].broadcast_address)):
    raise SystemExit('NODE_IP must be a usable address in MACHINE_CIDR')
if any(a.overlaps(b) for i,a in enumerate(nets) for b in nets[i+1:]):
    raise SystemExit('Machine, pod and service networks must not overlap')
if not disk.startswith('/dev/') or any(c.isspace() for c in disk):
    raise SystemExit('INSTALL_DISK must be a /dev/ target disk path')
if not re.fullmatch(r'[A-Za-z0-9._-]+',version): raise SystemExit('Invalid OKD_VERSION')
PY
}

target_run() {
  local cmd='bash -s --' arg
  for arg in "$@"; do printf -v cmd '%s %q' "$cmd" "$arg"; done
  ssh -F "$STATE_DIR/ssh_config" okd-target "$cmd"
}

remote_python() {
  local helper=$1 cmd arg launcher
  shift
  launcher='work=$1; interpreter=python3; if [[ -x "$work/oci-sdk/bin/python" ]]; then interpreter="$work/oci-sdk/bin/python"; fi; exec "$interpreter" - "$@"'
  printf -v cmd 'bash -c %q --' "$launcher"
  for arg in "$@"; do printf -v cmd '%s %q' "$cmd" "$arg"; done
  ssh -F "$STATE_DIR/ssh_config" okd-admin "$cmd" < "$helper"
}
