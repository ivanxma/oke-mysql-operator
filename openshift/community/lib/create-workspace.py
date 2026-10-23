"""Generate an isolated, directly executable OKD deployment folder."""
import argparse
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
REGIONS = ('uk-london-1', 'eu-frankfurt-1')
FIELDS = ('ADMIN_HOST','ADMIN_USER','ADMIN_WORKDIR','TARGET_USER','SSH_IDENTITY',
          'CLUSTER_NAME','BASE_DOMAIN','MACHINE_CIDR','POD_CIDR','SERVICE_CIDR',
          'INSTALL_DISK','OCI_REGION','OCI_AUTH','OCI_COMPARTMENT_ID','OCI_COMPARTMENT_NAME')


def metadata():
    try:
        request = urllib.request.Request('http://169.254.169.254/opc/v2/instance/',
                                         headers={'Authorization':'Bearer Oracle'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=2) as response:
            value=json.load(response)
            return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def ask(value, label, default, interactive):
    if value:
        return value
    if interactive:
        answer = input(label+(' ['+default+']' if default else '')+': ').strip()
        return answer or default
    if default:
        return default
    raise ValueError('Missing '+label+'. Supply its command option or run interactively.')


def load_profile(path):
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('Existing private profile must be a regular file with mode 600 or stricter')
    optional=path.with_name('community.optional.env')
    if optional.exists() and (optional.is_symlink() or optional.stat().st_mode & 0o077):
        raise ValueError('Optional private profile must be protected and not a symlink')
    keys=(*FIELDS,'OKD_STATE_DIR')
    code='import json,os; print(json.dumps({k:os.environ.get(k, "") for k in '+repr(keys)+'}))'
    shell='set -e; set -a; for name in '+ ' '.join(keys)+'; do unset "$name"; done; if [[ -f ${1%.env}.optional.env ]]; then source "${1%.env}.optional.env"; fi; source "$1"; python3 -c "$2"'
    result=subprocess.run(['bash','-c',shell,
                           '--',str(path),code],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


def validate(values):
    if values['OCI_REGION'] not in REGIONS or values['OCI_AUTH']!='instance_principal':
        raise ValueError('Select a supported region and instance-principal authentication')
    if not values['OCI_COMPARTMENT_ID'].startswith('ocid1.compartment.') or not values['OCI_COMPARTMENT_NAME']:
        raise ValueError('An explicitly authorized compartment ID and name are required')
    for key in ('CLUSTER_NAME','BASE_DOMAIN'):
        if not values[key] or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in values[key].split('.')):
            raise ValueError('Invalid '+key)
    if '.' in values['CLUSTER_NAME']:
        raise ValueError('CLUSTER_NAME must be one DNS label')
    networks=[ipaddress.IPv4Network(values[k]) for k in ('MACHINE_CIDR','POD_CIDR','SERVICE_CIDR')]
    if any(a.overlaps(b) for i,a in enumerate(networks) for b in networks[i+1:]):
        raise ValueError('Machine/pod/service CIDRs must not overlap')
    work=Path(values['ADMIN_WORKDIR'])
    if not work.is_absolute() or len(work.parts)<4 or work.parts[1]!='home' or '..' in work.parts:
        raise ValueError('Administration workspace must be a dedicated absolute directory under /home/')
    key=Path(values['SSH_IDENTITY']).expanduser()
    if not key.is_file() or key.stat().st_mode & 0o077:
        raise ValueError('SSH key must already exist on this host with mode 600 or stricter')
    for name in ('ADMIN_USER','TARGET_USER'):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*',values[name]):
            raise ValueError('Invalid Linux account in '+name)
    ipaddress.IPv4Address(values['ADMIN_HOST'])


def scripts(region):
    selector='''#!/usr/bin/env bash
# Source for direct oc commands; numbered launchers select this automatically.
_okd_generated_profile=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
_okd_generated_repo=$(cd -- "$_okd_generated_profile/../../../.." && pwd)
if source "$_okd_generated_repo/openshift/community/regions/REGION/env.sh" "$_okd_generated_profile/community.env"; then
  unset _okd_generated_profile _okd_generated_repo
else
  unset _okd_generated_profile _okd_generated_repo
  return 1 2>/dev/null || exit 1
fi
'''.replace('REGION',region)
    result={'00-env.sh':selector}
    targets={p.name:'openshift/community/'+p.name for p in (ROOT/'openshift/community').glob('[0-9][0-9]-*.sh')
             if p.name!='00-create-workspace.sh'}
    targets['00-check-config.sh']='openshift/community/regions/'+region+'/00-check-config.sh'
    for name,target in targets.items():
        result[name]='''#!/usr/bin/env bash
# Generated launcher: private profile is selected on every invocation.
set -euo pipefail
_okd_execution_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
_okd_code_root=$(cd -- "$_okd_execution_dir/../../../.." && pwd)
source "$_okd_execution_dir/00-env.sh"
[[ $OKD_CONFIG_FILE == "$_okd_execution_dir/community.env" ]] || { echo 'Wrong private profile selected.' >&2; exit 1; }
exec bash "$_okd_code_root/TARGET" "$@"
'''.replace('TARGET',target)
    return result


def generate(options, info=None):
    interactive=not options.non_interactive and sys.stdin.isatty()
    info=metadata() if info is None else info
    region=options.region or info.get('canonicalRegionName') or info.get('region') or ask(None,'OCI region','uk-london-1',interactive)
    if region not in REGIONS:
        raise ValueError('Region must be one of '+', '.join(REGIONS))
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}',options.name):
        raise ValueError('Deployment name must use lowercase letters, digits and hyphens')
    folder=ROOT/'internal_doc/regions'/region/options.name
    for path in (folder,*folder.parents):
        if path==ROOT:break
        if path.is_symlink():raise ValueError('Refusing symlink in private execution-folder path')
    profile=folder/'community.env'
    if profile.is_symlink() or (folder/'.state').is_symlink():
        raise ValueError('Refusing symlink configuration or deployment state')
    existing=profile.exists()
    if existing:
        values=load_profile(profile)
        if values['OCI_REGION']!=region:raise ValueError('Existing profile belongs to another region')
    else:
        uk=region=='uk-london-1'
        values=dict(ADMIN_HOST='127.0.0.1',ADMIN_USER=getpass.getuser(),TARGET_USER='core',
                    CLUSTER_NAME=options.cluster_name or 'okd-'+region+('' if options.name=='jumphost' else '-'+options.name),
                    POD_CIDR=options.pod_cidr or ('10.136.0.0/14' if uk else '10.140.0.0/14'),
                    SERVICE_CIDR=options.service_cidr or ('172.29.0.0/16' if uk else '172.28.0.0/16'),
                    INSTALL_DISK='/dev/sda',OCI_REGION=region,OCI_AUTH='instance_principal')
        values['SSH_IDENTITY']=str(Path(ask(options.ssh_key,'SSH private key path','',interactive)).expanduser().absolute())
        values['ADMIN_WORKDIR']=options.admin_workdir or str(Path.home()/('openshift_admin-'+region+'-'+options.name))
        values['BASE_DOMAIN']=ask(options.base_domain,'Controlled private DNS base domain','',interactive)
        values['MACHINE_CIDR']=ask(options.machine_cidr,'Private node-subnet CIDR','',interactive)
        values['OCI_COMPARTMENT_ID']=options.compartment_id or info.get('compartmentId') or ask(None,'Authorized compartment OCID','',interactive)
        values['OCI_COMPARTMENT_NAME']=ask(options.compartment_name,'Authorized compartment name','',interactive)
    validate(values)
    if values.get('OKD_STATE_DIR') and Path(values['OKD_STATE_DIR']).resolve()!=(folder/'.state').resolve():
        raise ValueError('Existing profile state is outside this generated folder; keep that deployment separate')
    if not existing:
        private=ROOT/'internal_doc/regions'
        for peer in private.rglob('community.env') if private.exists() else []:
            if peer==profile:continue
            other=load_profile(peer)
            if (other.get('CLUSTER_NAME'),other.get('BASE_DOMAIN'))==(values['CLUSTER_NAME'],values['BASE_DOMAIN']):
                raise ValueError('Cluster DNS identity already exists; choose --cluster-name')
            for name in ('POD_CIDR','SERVICE_CIDR'):
                candidate=ipaddress.IPv4Network(values[name])
                for other_name in ('MACHINE_CIDR','POD_CIDR','SERVICE_CIDR'):
                    if other.get(other_name) and candidate.overlaps(ipaddress.IPv4Network(other[other_name])):
                        raise ValueError('Planned overlay overlaps a saved deployment; choose --pod-cidr / --service-cidr')
    generated=scripts(region)
    generated['START-HERE.txt']='''OKD private execution folder

Run the numbered scripts HERE, one at a time. Each launcher selects this profile.
No manual file creation, configuration copying or environment sourcing is needed.

./00-check-config.sh --preparation
./01-prepare-host.sh
./02-create-bucket.sh
./02-download-okd.sh
./02-download-scos-image.sh
./03-build-control-plane-image.sh
./04-upload-image.sh

After image AVAILABLE, manually create a NEW private VM from it in OCI.
read -r -p "Actual new VM private IP: " node_ip
./05-register-node-ip.sh "$node_ip" --assigned-to-target
unset node_ip
./01-prepare-host.sh --ssh-only
./05-discover-dns-view.sh
./05-configure-dns.sh
./05-configure-dns.sh --apply
./05-verify-dns.sh --wait 600
# If SSH identity changed: confirm the target IP, then run
# ./05-refresh-target-host-key.sh --apply and repeat DNS verification.
./00-validate-environment.sh --existing-target
./05-wait-for-control-plane.sh
./06-check-control-plane.sh
./11-verify-web-access.sh

If monitoring repeatedly fails: ./05-diagnose-control-plane.sh (read-only).
For a confirmed latest controller-manager installer API-timeout failure only:
./05-recover-controller-manager.sh (preview; see docs/operations.md).
Recovery is conditional, not a required installation step.

For direct oc commands only: source ./00-env.sh
Do not repeat completed steps just because launchers were generated.
The generated files remain private; the shared implementation stays in the clone.
Review CIDR defaults against every connected network before image generation.
'''
    marker=folder/'.okd-workspace.json'
    if marker.is_symlink():raise ValueError('Refusing symlink workspace marker')
    saved=json.loads(marker.read_text()) if marker.exists() else {}
    if saved and (saved.get('region')!=region or saved.get('name')!=options.name):
        raise ValueError('Generated workspace scope mismatch')
    for name in generated:
        path=folder/name
        if path.is_symlink():raise ValueError('Refusing symlink generated launcher')
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=saved.get('files',{}).get(name):
            raise ValueError('Existing file would be overwritten: '+name+'; preserve/review it first')
    optional=folder/'community.optional.env'
    if optional.is_symlink():raise ValueError('Refusing symlink optional profile')
    if optional.exists() and optional.stat().st_mode & 0o077:
        raise ValueError('Optional private profile must have mode 600 or stricter')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700);folder.chmod(0o700)
    if not existing:
        with profile.open('x') as stream:
            stream.write('# Generated private configuration; never publish.\n'+''.join(k+'='+shlex.quote(values[k])+'\n' for k in FIELDS))
        profile.chmod(0o600)
    if not optional.exists():
        optional.write_text('# Private per-deployment state; no shared regional state.\nOKD_STATE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/.state"\n')
        optional.chmod(0o600)
    for name,contents in generated.items():
        path=folder/name;path.write_text(contents);path.chmod(0o700 if name.endswith('.sh') else 0o600)
    state=dict(schema=1,region=region,name=options.name,files={name:hashlib.sha256(contents.encode()).hexdigest() for name,contents in generated.items()})
    marker.write_text(json.dumps(state,indent=2)+'\n');marker.chmod(0o600)
    print('Private execution folder ready. '+('Existing configuration retained.' if existing else 'Configuration generated.'))
    print('No packages installed, no SSH connection and no OCI resources changed.')
    print('Next:\ncd '+shlex.quote(str(folder.relative_to(ROOT)))+'\n./00-check-config.sh --preparation\n./01-prepare-host.sh')
    return folder


def parser():
    p=argparse.ArgumentParser(description='Generate a private OKD execution folder; then run numbered scripts there.')
    p.add_argument('--region',choices=REGIONS)
    p.add_argument('--name',default='jumphost',help='Private folder name (default: jumphost)')
    p.add_argument('--non-interactive',action='store_true',help='Fail on missing required inputs instead of prompting')
    for flag in ('ssh-key','base-domain','machine-cidr','compartment-id','compartment-name','cluster-name','pod-cidr','service-cidr','admin-workdir'):
        p.add_argument('--'+flag)
    return p


if __name__=='__main__':
    os.umask(0o077)
    try:
        generate(parser().parse_args())
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('ERROR: '+str(error),file=sys.stderr)
        sys.exit(1)
