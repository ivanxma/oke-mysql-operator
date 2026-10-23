"""Guarded worker enrollment: explicit OCI VM, SSH metadata and exact CSR identity."""
import argparse
import base64
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID


def require(value,message):
    if not value:raise ValueError(message)


def csr_plan(resource,hostname,address,node,created):
    if any(c.get('type') in ('Approved','Denied') for c in resource.get('status',{}).get('conditions',[])):return None
    request=x509.load_pem_x509_csr(base64.b64decode(resource['spec']['request'],validate=True))
    names=request.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if len(names)!=1 or names[0].value!='system:node:'+hostname:return None
    require(request.is_signature_valid,'Worker CSR signature is invalid')
    require([x.value for x in request.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)]==['system:nodes'],
            'Worker CSR requested unexpected certificate groups')
    issued=datetime.datetime.fromisoformat(resource['metadata']['creationTimestamp'].replace('Z','+00:00'))
    require(issued>=created-datetime.timedelta(minutes=2),'CSR predates the explicitly selected VM')
    spec=resource['spec'];signer=spec.get('signerName');usages=set(spec.get('usages',[]))
    if signer=='kubernetes.io/kube-apiserver-client-kubelet':
        require(spec.get('username')=='system:serviceaccount:openshift-machine-config-operator:node-bootstrapper',
                'Only the verified worker bootstrap requestor may be approved')
        require('client auth' in usages and usages<={'client auth','digital signature','key encipherment'},'Unexpected client CSR usages')
        require(not list(request.extensions),'Bootstrap client CSR must not request additional extensions')
        return 'verified worker bootstrap client'
    if signer=='kubernetes.io/kubelet-serving':
        require(spec.get('username')=='system:node:'+hostname and 'system:nodes' in spec.get('groups',[]),'Serving CSR requestor differs from verified node')
        require(node and node['metadata']['name']==hostname and any(a.get('type')=='InternalIP' and a.get('address')==address for a in node['status'].get('addresses',[])),
                'Serving CSR requires the matching node and private address')
        require('server auth' in usages and usages<={'server auth','digital signature','key encipherment'},'Unexpected serving CSR usages')
        san=request.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        require(all(e.oid==ExtensionOID.SUBJECT_ALTERNATIVE_NAME for e in request.extensions),'Serving CSR requests unexpected certificate extensions')
        ips={str(x) for x in san.get_values_for_type(x509.IPAddress)}
        dns=set(san.get_values_for_type(x509.DNSName))
        require(ips=={address} and dns<={hostname,hostname.split('.')[0]} and len(san)==len(ips)+len(dns),'Serving CSR SANs exceed this worker identity')
        return 'verified worker kubelet serving'
    raise ValueError('Unexpected CSR signer for the selected worker')


PROBE='''import json,socket,sys,urllib.request
address,cid,instance_id=sys.argv[1:]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def metadata(path):
 r=urllib.request.Request('http://169.254.169.254/opc/v2/'+path,headers={'Authorization':'Bearer Oracle'})
 with opener.open(r,timeout=5) as response:return json.load(response)
i=metadata('instance/');v=metadata('vnics/')
if i.get('id')!=instance_id or i.get('compartmentId')!=cid or not any(x.get('privateIp')==address for x in v):raise SystemExit('SSH metadata mismatch')
print(json.dumps(dict(hostname=socket.gethostname().lower().rstrip('.'),instance_id=i['id'])))
'''


def save(path,value):
    require(not path.is_symlink(),'Refusing symlink worker state')
    temp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(value)
    temp.replace(path)


def main():
    p=argparse.ArgumentParser();p.add_argument('address');p.add_argument('--approve',action='store_true');p.add_argument('--wait',type=int,default=1200);p.add_argument('--refresh-host-key',action='store_true')
    args=p.parse_args();require(1<=args.wait<=3600,'Wait must be 1..3600 seconds')
    require(not args.refresh_host_key or args.approve,'Host-key refresh requires --approve')
    env=os.environ;work=Path(env['ADMIN_WORKDIR']);state=Path(env['OKD_STATE_DIR']);cid=env['OCI_COMPARTMENT_ID'];region=env['OCI_REGION']
    require(ipaddress.ip_address(args.address) in ipaddress.ip_network(env['MACHINE_CIDR']) and args.address!=env['NODE_IP'],'Select a worker IP in this private machine subnet')
    selected=Path(env['KUBECONFIG']);require(selected.is_file() and selected.samefile(work/'sno/auth/kubeconfig'),'Configured installation KUBECONFIG is required')
    oc=[str(work/'bin/oc'),'--kubeconfig',str(selected),'--request-timeout=20s']
    def command(*a,data=None):return subprocess.check_output(oc+list(a),input=data,text=True,timeout=30)
    domain=env['CLUSTER_NAME']+'.'+env['BASE_DOMAIN']
    require(command('config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}').strip()=='https://api.'+domain+':6443','Wrong cluster selected')
    uid=json.loads(command('get','namespace','kube-system','-o','json'))['metadata']['uid']
    media=json.loads((work/'worker-image/oci-worker-image.json').read_text());build=json.loads((work/'worker-image/build.json').read_text())
    net=json.loads((state/'worker-network.json').read_text())
    require(media['compartment_id']==cid and media['region']==region and build['cluster_domain']==domain and build['role']=='worker' and net['cluster_uid']==uid,'Worker image/network state scope mismatch')
    import oci
    options=dict(signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    network=oci.core.VirtualNetworkClient({'region':region},**options);compute=oci.core.ComputeClient({'region':region},**options)
    subnet=network.get_subnet(net['subnet_id']).data;require(subnet.compartment_id==cid and subnet.cidr_block==env['MACHINE_CIDR'],'Subnet scope mismatch')
    ips=oci.pagination.list_call_get_all_results(network.list_private_ips,subnet_id=subnet.id,ip_address=args.address).data
    require(len(ips)==1,'Worker private IP assignment must be unique')
    vnic=network.get_vnic(ips[0].vnic_id).data
    require(vnic.compartment_id==cid and not vnic.public_ip and net['nsg_id'] in (vnic.nsg_ids or []),'Worker VNIC scope/NSG mismatch; run step 08 with --worker-ip')
    links=[a for a in oci.pagination.list_call_get_all_results(compute.list_vnic_attachments,cid).data if a.vnic_id==vnic.id and a.lifecycle_state=='ATTACHED']
    require(len(links)==1,'Worker attachment must identify one VM')
    instance=compute.get_instance(links[0].instance_id).data
    require(instance.compartment_id==cid and instance.image_id==media['image_id'] and instance.lifecycle_state=='RUNNING','Worker VM/image/state mismatch')
    if args.refresh_host_key:
        known=state/'known_hosts';require(not known.is_symlink(),'Refusing symlink known_hosts')
        if known.exists():
            backup=state/('known_hosts.worker-backup-'+str(int(time.time())));shutil.copy2(known,backup)
            subprocess.run(['ssh-keygen','-R',args.address,'-f',str(known)],check=True,stdout=subprocess.DEVNULL)
    deadline=time.monotonic()+args.wait
    print('Verified OCI worker VM/image/NSG. '+('CSR approval enabled for this worker only.' if args.approve else 'READ-ONLY preview; no approvals.'),flush=True)
    while True:
        result=subprocess.run(['ssh','-F',str(state/'ssh_config'),'-o','HostName='+args.address,'okd-target',
            shlex.join(['python3','-',args.address,cid,instance.id])],input=PROBE,text=True,capture_output=True,timeout=40)
        if result.returncode:
            if 'host key verification failed' in result.stderr.lower() or 'remote host identification has changed' in result.stderr.lower():
                raise ValueError('Worker host identity changed. Verify the intended VM, then rerun with --approve --refresh-host-key')
            print('WAITING: worker SSH/metadata probe; OS may still be installing or rebooting.',flush=True)
        else:
            host=json.loads(result.stdout)['hostname'];require(bool(re.fullmatch(r'[a-z0-9.-]+',host)),'Unexpected worker hostname')
            nodes=json.loads(command('get','nodes','-o','json'))['items'];matches=[n for n in nodes if n['metadata']['name']==host]
            node=matches[0] if len(matches)==1 else None
            pending=json.loads(command('get','csr','-o','json'))['items'];plans=[]
            for csr in pending:
                why=csr_plan(csr,host,args.address,node,instance.time_created)
                if why:plans.append((csr,why))
            require(len([c for c,w in plans if c['spec']['signerName']=='kubernetes.io/kube-apiserver-client-kubelet'])<=1,'Multiple pending bootstrap CSRs for this hostname; inspect instead of bulk approving')
            for csr,why in plans:
                print(('APPROVE ' if args.approve else 'PLAN ') +csr['metadata']['name']+': '+why,flush=True)
                if args.approve:
                    require(json.loads(command('get','namespace','kube-system','-o','json'))['metadata']['uid']==uid,'Cluster changed before approval')
                    csr.setdefault('status',{}).setdefault('conditions',[]).append(dict(type='Approved',status='True',reason='VerifiedOCIWorker',message='Verified selected OCI worker, SSH metadata, hostname and CSR identity',lastUpdateTime=datetime.datetime.now(datetime.timezone.utc).isoformat()))
                    command('replace','--raw=/apis/certificates.k8s.io/v1/certificatesigningrequests/'+csr['metadata']['name']+'/approval','-f','-',data=json.dumps(csr))
            ready=node and any(c['type']=='Ready' and c['status']=='True' for c in node['status'].get('conditions',[]))
            if ready and not plans:
                require(any(a.get('type')=='InternalIP' and a.get('address')==args.address for a in node['status'].get('addresses',[])),'Ready worker address differs')
                if args.approve:
                    path=state/'worker-nodes.json';record=json.loads(path.read_text()) if path.exists() else dict(cluster_uid=uid,workers={})
                    require(record['cluster_uid']==uid,'Saved workers belong to another cluster')
                    record['workers'][instance.id]=dict(name=host,address=args.address,node_uid=node['metadata']['uid'])
                    save(path,json.dumps(record,indent=2)+'\n')
                    count=1+len(record['workers'])
                    guards={'OCI_REGION':region,'OCI_COMPARTMENT_ID':cid,'CLUSTER_NAME':env['CLUSTER_NAME'],'BASE_DOMAIN':env['BASE_DOMAIN'],'NODE_IP':env['NODE_IP']}
                    text='# Generated expected node count for verified workers.\n'
                    for key,value in guards.items():text+='[[ ${'+key+':-} == '+shlex.quote(value)+' ]] || { echo "Worker registration scope mismatch" >&2; return 1; }\n'
                    text+='if (( ${EXPECTED_NODE_COUNT:-1} < '+str(count)+' )); then export EXPECTED_NODE_COUNT='+str(count)+'; fi\n'
                    save(state/'worker-nodes.env',text)
                print('READY: verified worker '+host+'. '+('Registration saved; rerun step 06 acceptance.' if args.approve else 'Preview finished; no state changes.'),flush=True)
                return
            print('WAITING: '+host+' node/CSR readiness; only matching worker requests are considered.',flush=True)
        if not args.approve:return
        require(time.monotonic()<deadline,'Worker readiness timeout; installation continues. No unrelated CSRs were approved')
        time.sleep(15)


if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except Exception as error:sys.exit('Worker registration stopped: '+str(error))
