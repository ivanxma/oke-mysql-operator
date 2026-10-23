#!/usr/bin/env bash
# Read-only remote checks. Reports and temporary SSH state stay in private storage.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./00-validate-environment.sh [--existing-target]'
  echo 'Default: planned target; checks admin/config/DNS without requiring a target VM.'
  echo 'Reads OKD_CONFIG_FILE; checks hosts, capacity, DNS, egress, OCI routes and security.'
  echo 'Exit 0: checks pass; 1: failures; 2: warnings/unverified prerequisites.'
  exit 0
fi
planned=true
if [[ ${1:-} == --existing-target ]]; then planned=false; shift; fi
[[ $# -eq 0 ]] || fail 'Use --existing-target or no arguments; configure deployment values privately.'
load_config
need ssh; need python3
validate_host "$ADMIN_USER" "$ADMIN_HOST"
TARGET_USER=${TARGET_USER:-opc}
validate_host "$TARGET_USER" "$NODE_IP"
[[ -f ${SSH_IDENTITY:-} ]] || fail 'Set SSH_IDENTITY to an existing local private key in the private config.'
key_path=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$SSH_IDENTITY")
[[ $key_path != *'"'* && $key_path != *$'\n'* ]] || fail 'Unsupported character in SSH key path.'
umask 077
mkdir -p "$STATE_DIR"
validation_tmp=$(mktemp -d "$STATE_DIR/validation.XXXXXX")
trap 'rm -rf -- "$validation_tmp"' EXIT
# First-seen host keys are recorded privately; changed host keys are rejected.
cat > "$validation_tmp/ssh_config" <<CONFIG
Host okd-admin
  HostName $ADMIN_HOST
  User $ADMIN_USER
  IdentityFile "$key_path"
  IdentitiesOnly yes
  ForwardAgent no
  BatchMode yes
  ConnectTimeout 10
  StrictHostKeyChecking accept-new
  UserKnownHostsFile "$STATE_DIR/known_hosts"
Host okd-target
  HostName $NODE_IP
  User $TARGET_USER
  IdentityFile "$key_path"
  IdentitiesOnly yes
  ForwardAgent no
  ProxyJump okd-admin
  BatchMode yes
  ConnectTimeout 10
  StrictHostKeyChecking accept-new
  UserKnownHostsFile "$STATE_DIR/known_hosts"
CONFIG
python3 - "$CLUSTER_NAME" "$BASE_DOMAIN" "$NODE_IP" "$MACHINE_CIDR" "$POD_CIDR" \
  "$SERVICE_CIDR" "$INSTALL_DISK" "$OKD_VERSION" "$ADMIN_WORKDIR" "$OCI_REGION" \
  "$OCI_AUTH" "${CLIENT_CIDR:-}" "$planned" > "$validation_tmp/config.json" <<'PY'
import json,sys
keys=('CLUSTER_NAME','BASE_DOMAIN','NODE_IP','MACHINE_CIDR','POD_CIDR','SERVICE_CIDR',
      'INSTALL_DISK','OKD_VERSION','ADMIN_WORKDIR','OCI_REGION','OCI_AUTH','CLIENT_CIDR','PLANNED_TARGET')
print(json.dumps(dict(zip(keys,sys.argv[1:]))))
PY
python3 - "$validation_tmp/config.json" "$key_path" > "$validation_tmp/config-checks.json" <<'PY'
import ipaddress,json,re,stat,sys
cfg=json.load(open(sys.argv[1]));checks=[]
def add(status,name,detail):checks.append(dict(status=status,check=name,detail=detail))
try:
 nets=[ipaddress.IPv4Network(cfg[k]) for k in ('MACHINE_CIDR','POD_CIDR','SERVICE_CIDR')]
 node=ipaddress.IPv4Address(cfg['NODE_IP'])
 if node not in nets[0]:raise ValueError('NODE_IP is outside MACHINE_CIDR')
 if any(a.overlaps(b) for i,a in enumerate(nets) for b in nets[i+1:]):raise ValueError('Machine, pod and service networks overlap')
 add('PASS','Address ranges','Configured machine/pod/service CIDRs do not overlap; node belongs to its subnet.')
except ValueError as e:add('FAIL','Address ranges',str(e))
label=r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
if not re.fullmatch(label,cfg['CLUSTER_NAME']):add('FAIL','Cluster name','Use a valid lowercase DNS label.')
domain=cfg['BASE_DOMAIN']
if not domain or not all(re.fullmatch(label,x) for x in domain.split('.')) or domain.endswith('example.com'):
 add('FAIL','DNS base domain','Set BASE_DOMAIN to a controlled real private DNS domain before generating images.')
else:add('PASS','DNS base domain','DNS domain is configured; host checks verify the records.')
if cfg.get('CLIENT_CIDR'):
 try:ipaddress.IPv4Network(cfg['CLIENT_CIDR']);add('PASS','Browser/client CIDR','Client CIDR is configured for ingress policy checks.')
 except ValueError:add('FAIL','Browser/client CIDR','CLIENT_CIDR must be a valid IPv4 network.')
else:add('WARN','Browser/client CIDR','Set CLIENT_CIDR to validate the intended corporate browser/API source range.')
import os
mode=stat.S_IMODE(os.stat(sys.argv[2]).st_mode)
add('PASS' if mode & 0o077 == 0 else 'FAIL','Local SSH key permissions',
    'Key permissions restrict group/other access.' if mode & 0o077 == 0 else 'Remove group/other permissions on the configured local SSH private key.')
if cfg.get('PLANNED_TARGET') == 'true':
 add('ACTION','CoreOS installation handoff','Launch a NEW VM from the imported CoreOS installer image. No OL9 target or automated deletion is required.')
else:
 add('ACTION','Cluster installation monitoring','Existing target selected. After preflight, run step 05 monitoring and step 06 acceptance; no new VM or reinstallation is requested.')
print(json.dumps(dict(role='configuration',checks=checks,facts={})))
PY
cfg_json=$(cat "$validation_tmp/config.json")
remote_check() {
  local alias=$1 role=$2 target_facts=${3:-'{}'} cmd arg status=0
  cmd='python3 -'
  for arg in "$cfg_json" "$role" "$target_facts"; do printf -v cmd '%s %q' "$cmd" "$arg"; done
  printf 'Checking %s (read-only)...\n' "$role"
  ssh -F "$validation_tmp/ssh_config" "$alias" "$cmd" \
    < "$COMMUNITY_DIR/lib/validate-host.py" > "$validation_tmp/$role.json" \
    2> "$validation_tmp/$role-ssh.log" || status=$?
  python3 - "$validation_tmp/$role.json" "$role" "$status" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
try:json.loads(p.read_text())
except (ValueError,OSError):
 p.write_text(json.dumps(dict(role=sys.argv[2],facts={},checks=[dict(status='FAIL',check='SSH / remote Python',
 detail='Read-only host check failed. Verify SSH key, access route, account, host key and Python 3. See the private SSH log. Exit '+sys.argv[3])])) )
PY
}
if $planned; then
  printf '%s\n' '{"role":"target","facts":{},"checks":[{"status":"ACTION","check":"Planned VM","detail":"No target SSH attempted. Verify planned OCI capacity, subnet, routes, DHCP and security before launch; rerun with --existing-target after launch."}]}' > "$validation_tmp/target.json"
else
  remote_check okd-target target
fi
target_facts=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])).get("facts",{})))' "$validation_tmp/target.json")
remote_check okd-admin admin "$target_facts"
report_path="$STATE_DIR/validation-$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"
# Preserve SSH error output privately; the console summary does not echo connection details.
for role in target admin; do
  if [[ -s "$validation_tmp/$role-ssh.log" ]]; then
    cp "$validation_tmp/$role-ssh.log" "$STATE_DIR/validation-$role-ssh.log"
  fi
done
set +e
python3 - "$report_path" "$validation_tmp/config-checks.json" "$validation_tmp/target.json" "$validation_tmp/admin.json" <<'PY'
import collections,json,sys
from pathlib import Path
lines=['OKD / OCI pre-installation validation (read-only)','']
counts=collections.Counter()
for name in sys.argv[2:]:
 report=json.load(open(name));lines.append(report['role'].upper())
 for check in report['checks']:
  counts[check['status']]+=1
  lines.append('[{status}] {check}: {detail}'.format(**check))
 lines.append('')
lines.append('Summary: '+', '.join(str(counts[x])+' '+x for x in ('PASS','FAIL','WARN','ACTION')))
if counts['FAIL']:lines.append('NOT READY: correct FAIL items, then repeat validation. WARN items need review.')
elif counts['WARN']:lines.append('INCOMPLETE: host checks completed, but WARN items are not yet proven. Review required actions before installation.')
else:lines.append('Host checks pass. Complete ACTION items and the explicit CoreOS provisioning handoff.')
text='\n'.join(lines)+'\n';Path(sys.argv[1]).write_text(text);print(text,end='')
sys.exit(1 if counts['FAIL'] else 2 if counts['WARN'] else 0)
PY
result=$?
set -e
printf '\nPrivate report saved under OKD_STATE_DIR: %s\n' "$(basename "$report_path")"
exit "$result"
