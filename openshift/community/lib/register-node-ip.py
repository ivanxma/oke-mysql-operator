#!/usr/bin/env python3
"""Record an operator-supplied target IP; never discover, choose or reserve one."""
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys

FIELDS=('region','compartment_id','admin_workdir','cluster_name','base_domain','machine_cidr')

def save(path, text):
    temp=path.with_name(path.name+'.tmp-'+secrets.token_hex(4))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(text)
    temp.replace(path)


def validate(record, scope):
    if any(record.get(k)!=scope[k] for k in FIELDS):
        raise ValueError('Target address registration belongs to another region, compartment, workspace or cluster')
    address=ipaddress.IPv4Address(record['node_ip'])
    network=ipaddress.IPv4Network(scope['machine_cidr'])
    if address not in network or address in (network.network_address,network.network_address+1,network.broadcast_address):
        raise ValueError('Explicit target IP must be a usable OCI address in the configured private machine subnet')


def environment(record):
    lines=['# Generated private target registration. No automatic address allocation.']
    keys={'region':'OCI_REGION','compartment_id':'OCI_COMPARTMENT_ID','admin_workdir':'ADMIN_WORKDIR',
          'cluster_name':'CLUSTER_NAME','base_domain':'BASE_DOMAIN','machine_cidr':'MACHINE_CIDR'}
    for field,key in keys.items():
        lines += ['if [[ ${'+key+':-} != '+shlex.quote(record[field])+' ]]; then',
                  "  printf 'Target address registration does not match selected "+key+".\\n' >&2",
                  '  return 1 2>/dev/null || exit 1','fi']
    lines += ['if [[ -n ${NODE_IP:-} && $NODE_IP != '+shlex.quote(record['node_ip'])+' ]]; then',
              "  printf 'Configured NODE_IP conflicts with the registered target.\\n' >&2",
              '  return 1 2>/dev/null || exit 1','fi','export NODE_IP='+shlex.quote(record['node_ip'])]
    return '\n'.join(lines)+'\n'


def register(work, cid, region, cluster, domain, machine, address):
    root=Path(work)
    if not root.is_absolute() or not root.is_dir() or not (root/'env.sh').is_file():
        raise ValueError('Prepare the admin workspace with step 01 before registering the target')
    record=dict(region=region,compartment_id=cid,admin_workdir=work,cluster_name=cluster,
                base_domain=domain,machine_cidr=machine,node_ip=address,
                availability='operator-confirmed assigned target VM address; not reserved or discovered by this script')
    validate(record,record)
    path=root/'oci-node-ip.json'
    if path.exists():
        previous=json.loads(path.read_text());validate(previous,record)
        if previous['node_ip']!=address:raise ValueError('A different target IP is already registered; retain state and review before changing it')
    elif (root/'sno').exists():
        # The cluster-specific image is built/imported BEFORE an actual VM/IP exists.
        # Permit its first target registration only against that verified image scope.
        build_path=root/'control-plane-build.json'; image_path=root/'oci-control-plane-image.json'
        if not build_path.is_file() or not image_path.is_file():
            raise ValueError('Installer state exists without a verified custom-image import; preserve and review it')
        build=json.loads(build_path.read_text()); image=json.loads(image_path.read_text())
        if (build.get('cluster_domain')!=cluster+'.'+domain or build.get('role')!='control-plane' or
                build.get('machine_cidr')!=machine or image.get('compartment_id')!=cid or
                image.get('region')!=region or image.get('image_state')!='AVAILABLE'):
            raise ValueError('Installer state/custom image does not match this target registration scope')
    save(path,json.dumps(record,indent=2)+'\n')
    save(root/'oci-node-ip.env',environment(record))
    # Upgrade an already-prepared workspace env without changing kubeconfig selection.
    start='# BEGIN OKD GENERATED TARGET REGISTRATION'
    end='# END OKD GENERATED TARGET REGISTRATION'
    block=start+'\n'+''.join('export '+key+'='+shlex.quote(record[field])+'\n' for field,key in
               [('cluster_name','CLUSTER_NAME'),('base_domain','BASE_DOMAIN'),('machine_cidr','MACHINE_CIDR')])
    block+='unset NODE_IP\nsource "$ADMIN_WORKDIR/oci-node-ip.env" || { return 1 2>/dev/null || exit 1; }\n'+end+'\n'
    envpath=root/'env.sh';text=envpath.read_text()
    text=re.sub(re.escape(start)+r'.*?'+re.escape(end)+r'\n?', '',text,flags=re.S)
    save(envpath,text.rstrip()+'\n'+block)
    return record


def ingest(receipt, state, scope):
    validate(receipt,scope)
    root=Path(state);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/'node-ip.json'
    if path.exists():
        old=json.loads(path.read_text());validate(old,scope)
        if old['node_ip']!=receipt['node_ip']:raise ValueError('Existing local target record selects another address')
    save(path,json.dumps(receipt,indent=2)+'\n');save(root/'node-ip.env',environment(receipt))


def main():
    if len(sys.argv)==11 and sys.argv[1]=='--ingest':
        _,_,receipt,state,work,cid,region,cluster,domain,machine,address=sys.argv
        scope=dict(zip(FIELDS,[region,cid,work,cluster,domain,machine]))
        record=json.loads(Path(receipt).read_text())
        if record.get('node_ip')!=address:raise ValueError('Receipt differs from the explicitly supplied IP')
        ingest(record,state,scope)
        print('Explicit target IP registered. Source this region\x27s env.sh to ingest NODE_IP; no VM/IP allocation or DNS changes made.')
    elif len(sys.argv)==8:
        print(json.dumps(register(*sys.argv[1:])))
    else:raise ValueError('Invalid registration arguments')


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError) as error:sys.exit('Target registration failed: '+str(error))
