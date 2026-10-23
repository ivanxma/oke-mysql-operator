#!/usr/bin/env python3
"""London-first Submariner lab: explicit kubeconfigs, guarded peer enrollment."""
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import urllib.request


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp=path.with_name(path.name+'.tmp-'+secrets.token_hex(4))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(json.dumps(data,indent=2)+'\n')
    temp.replace(path)


def run(args, data=None):
    return subprocess.check_output(args,input=data,text=True,stderr=None)


class Workflow:
    def __init__(self, env=None):
        self.env=dict(os.environ if env is None else env)
        self.state=Path(self.env['SUBMARINER_STATE_DIR'])
        work=Path(self.env['ADMIN_WORKDIR'])
        profile_state=Path(self.env.get('OKD_STATE_DIR','/nonexistent-profile-state'))
        require(self.state.is_absolute() and '..' not in self.state.parts and
                (profile_state in self.state.parents or work/'internal_doc' in self.state.parents),
                'Keep networking state under the selected private OKD_STATE_DIR; explicit legacy admin state remains supported.')
        require(not self.state.is_symlink(),'Refusing symlink networking state.')
        self.version=self.env.get('SUBMARINER_VERSION','0.24.2')
        require(bool(re.fullmatch(r'\d+\.\d+\.\d+',self.version)),'Pin a stable Submariner version.')
        self.bin=self.state/'bin/subctl'
        self.broker=self.state/'broker-info.subm'

    def initialize(self):
        """Discover parameters before any gateway selection or network mutation."""
        require(self.env.get('OCI_REGION')=='uk-london-1','Initialize the London profile first.')
        base,_,_=self.selection()
        require(run(base+['get','--raw=/readyz']).strip()=='ok','Selected API is not ready.')
        network=self.read(False,'get','network.config.openshift.io','cluster')
        nodes=self.read(False,'get','nodes')['items']
        operators=self.read(False,'get','clusteroperators')['items']
        pools=self.read(False,'get','machineconfigpools')['items']
        uid=self.read(False,'get','namespace','kube-system')['metadata']['uid']
        def settled(items, expected):
            return bool(items) and all(all({c['type']:c['status'] for c in item.get('status',{}).get('conditions',[])}.get(k)==v
                                          for k,v in expected.items()) for item in items)
        ready=settled(nodes,{'Ready':'True'}) and settled(operators,{'Available':'True','Progressing':'False','Degraded':'False'}) and settled(pools,{'Updated':'True','Updating':'False','Degraded':'False'})
        workers=[n['metadata']['name'] for n in nodes if
                 n['metadata'].get('labels',{}).get('kubernetes.io/os')=='linux' and
                 'node-role.kubernetes.io/worker' in n['metadata'].get('labels',{}) and
                 not any(k in n['metadata'].get('labels',{}) for k in ('node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane')) and
                 not n['spec'].get('unschedulable') and not n['spec'].get('taints') and
                 any(c['type']=='Ready' and c['status']=='True' for c in n['status'].get('conditions',[]))]
        profile=Path(self.env['OKD_CONFIG_FILE']).parent
        config=Path(self.env['SUBMARINER_CONFIG_FILE'])
        require(config.parent==profile and not config.is_symlink(),'Keep Submariner config beside the selected private OKD profile.')
        discovery=self.state/'discovery.json'
        if discovery.exists():
            require(not discovery.is_symlink() and json.loads(discovery.read_text())['uid']==uid,
                    'Networking discovery belongs to another cluster; preserve and review before reuse.')
        values=dict(SUBMARINER_VERSION=self.version,SUBMARINER_TARGET_ID=self.env['CLUSTER_NAME'],
                    SUBMARINER_TARGET_GATEWAY=workers[0] if len(workers)==1 else '',
                    SUBMARINER_LONDON_INSTALLATION_VERIFIED=str(ready).lower(),
                    SUBMARINER_COMPATIBILITY_REVIEWED='false',SUBMARINER_NETWORK_REVIEWED='false',SUBMARINER_ENABLE_PEER='false')
        import shlex
        if not config.exists():
            fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:
                stream.write('# Generated Submariner profile. Live parameters discovered; reviews are explicit.\n')
                for k,v in values.items():stream.write(k+'='+shlex.quote(v)+'\n')
        else:
            require(config.stat().st_mode & 0o077==0,'Private Submariner config must restrict group/other access.')
            text=config.read_text()
            # Fill only an empty generated gateway when a single actual worker appears.
            if text.startswith('# Generated Submariner profile.') and len(workers)==1:
                text=re.sub(r"(?m)^SUBMARINER_TARGET_GATEWAY=(?:''|\"\")?$",
                            'SUBMARINER_TARGET_GATEWAY='+shlex.quote(workers[0]),text)
                config.write_text(text)
        optional=config.with_name(config.stem+'.optional.env')
        require(not optional.is_symlink(),'Refusing symlink optional configuration.')
        if not optional.exists():
            fd=os.open(optional,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:
                stream.write((Path(__file__).parents[1]/'config.optional.env.example').read_text())
        private_write(discovery,dict(uid=uid,api=self.env['SUBMARINER_TARGET_API'],network=network['spec'],
                                    machine_cidr=self.env['MACHINE_CIDR'],worker_candidates=workers,health_settled=ready))
        directory=profile/'networking/submariner'
        require(not directory.is_symlink() and directory.resolve()==profile.resolve()/'networking/submariner','Refusing symlink execution folder.')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        for source in Path(__file__).parents[1].glob('[0-9][0-9]-*.sh'):
            target=directory/source.name
            marker='# Generated Submariner launcher.'
            require(not target.is_symlink() and (not target.exists() or marker in target.read_text()),'Preserve unrecognized private launcher: '+source.name)
            target.write_text('''#!/usr/bin/env bash
'''+marker+'''
set -euo pipefail
profile=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
repo=$(cd -- "$profile/../../../.." && pwd)
source "$profile/00-env.sh"
exec bash "$repo/openshift/community/networking/submariner/'''+source.name+'''" "$@"
''')
            target.chmod(0o700)
        print('Private Submariner execution folder: '+str(directory))
        print('Live CIDRs/API/cluster identity discovered; existing settings retained. No cluster/OCI changes.')
        print('Eligible worker gateways: '+(', '.join(workers) or 'NONE — create/join a SCOS worker before step 03.'))
        print('Next: run ./01-install-subctl.sh there; review compatibility/networking before steps 02–05.')

    def enabled(self, key):
        return self.env.get(key)=='true'

    def review_local(self):
        """Explicit local-network acknowledgement plus live compatibility/health checks."""
        require(not self.enabled('SUBMARINER_ENABLE_PEER'),'Local prerequisite review requires peer enrollment disabled.')
        self.sub(['diagnose','k8s-version'])
        base,_,_=self.selection()
        pods=self.read(False,'get','pods','-n','openshift-ovn-kubernetes')['items']
        candidates=[p for p in pods if p.get('status',{}).get('phase')=='Running' and
                    any(c['name']=='nbdb' for c in p['spec']['containers'])]
        require(bool(candidates),'No running OVN Northbound DB container found.')
        schema=run(base+['-n','openshift-ovn-kubernetes','exec',candidates[0]['metadata']['name'],'-c','nbdb','--',
                         'ovsdb-client','get-schema-version','unix:/var/run/ovn/ovnnb_db.sock','OVN_Northbound']).strip()
        require(bool(re.fullmatch(r'\d+\.\d+\.\d+',schema)) and tuple(map(int,schema.split('.')))>=(6,1,0),
                'OVN Northbound schema must be >=6.1.0.')
        for name in ('SUBMARINER_LONDON_INSTALLATION_VERIFIED','SUBMARINER_COMPATIBILITY_REVIEWED','SUBMARINER_NETWORK_REVIEWED'):
            self.env[name]='true'
        info=self.validation()
        config=Path(self.env['SUBMARINER_CONFIG_FILE'])
        require(not config.is_symlink() and config.stat().st_mode & 0o077==0,'Private networking config must be protected.')
        text=config.read_text()
        for key in ('SUBMARINER_LONDON_INSTALLATION_VERIFIED','SUBMARINER_COMPATIBILITY_REVIEWED','SUBMARINER_NETWORK_REVIEWED'):
            text,count=re.subn(r'(?m)^(?:export\s+)?'+key+r'=.*$',key+'=true',text)
            require(count==1,'Need exactly one '+key+' setting in selected private profile.')
        temporary=config.with_name(config.name+'.tmp-'+secrets.token_hex(4))
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(text)
        temporary.replace(config)
        private_write(self.state/'prerequisite-review.json',dict(cluster_uid=info['target']['uid'],ovn_nb_schema=schema,
                     version=self.version,scope='London only; operator acknowledged local network review; peer paths unverified'))
        print('PASS: version diagnostic, OVN schema '+schema+', cluster health and worker gateway. Local prerequisite review recorded; peer networking remains unverified.')

    def peer_allowed(self):
        require(self.enabled('SUBMARINER_ENABLE_PEER'),'Peer phase disabled: London-only mode. Set SUBMARINER_ENABLE_PEER=true only when ready to change the peer.')
        require(self.enabled('SUBMARINER_PEER_INSTALLATION_VERIFIED'),'Verify the peer installation before enrollment.')

    def selection(self, peer=False):
        prefix='SUBMARINER_PEER' if peer else 'SUBMARINER_TARGET'
        path=Path(self.env.get(prefix+'_KUBECONFIG','') if peer else self.env.get('KUBECONFIG',''))
        require(path.is_absolute() and path.is_file() and ':' not in str(path),'Configured kubeconfig must exist as one absolute file on this admin host.')
        require(path.resolve()!= (Path.home()/'.kube/config').resolve(),'Default ~/.kube/config is forbidden.')
        require(path.stat().st_mode & 0o077 == 0,'Kubeconfig must have mode 600 or stricter.')
        context=self.env.get(prefix+'_CONTEXT') or run(['oc','--kubeconfig',str(path),'config','current-context']).strip()
        require(bool(context),'Explicit kubeconfig has no current context.')
        base=['oc','--kubeconfig',str(path),'--context',context,'--request-timeout=30s']
        actual=run(base+['config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}']).strip()
        expected=self.env.get(prefix+'_API','')
        require(bool(expected) and actual==expected,'Selected API does not match the private networking configuration.')
        return base,path,context

    def read(self, peer, *args):
        base,_,_=self.selection(peer)
        return json.loads(run(base+list(args)+['-o','json']))

    def health(self, peer=False):
        if peer: self.peer_allowed()
        else:
            require(self.env.get('OCI_REGION')=='uk-london-1','Initial networking target must be London.')
            require(self.enabled('SUBMARINER_LONDON_INSTALLATION_VERIFIED'),'Complete London installation and acceptance before networking.')
        require(self.enabled('SUBMARINER_COMPATIBILITY_REVIEWED'),'Review exact OKD/Kubernetes/Submariner and OVN NB DB >=6.1 compatibility first.')
        require(self.enabled('SUBMARINER_NETWORK_REVIEWED'),'Review DRG/routes, gateway ports, DNS and MTU before networking.')
        base,_,_=self.selection(peer)
        require(run(base+['get','--raw=/readyz']).strip()=='ok','API is not ready.')
        operators=self.read(peer,'get','clusteroperators')['items']
        require(bool(operators),'No OKD cluster operators found.')
        for op in operators:
            conditions={c['type']:c['status'] for c in op['status']['conditions']}
            require(conditions.get('Available')=='True' and conditions.get('Degraded')=='False' and conditions.get('Progressing')=='False',
                    'Settle every cluster operator before changing networking.')
        pools=self.read(peer,'get','machineconfigpools')['items']
        require(bool(pools),'No machine-config pools found.')
        for pool in pools:
            conditions={c['type']:c['status'] for c in pool['status']['conditions']}
            require(conditions.get('Updated')=='True' and conditions.get('Degraded')=='False' and conditions.get('Updating')=='False',
                    'Machine-config pools must be settled.')
        network=self.read(peer,'get','network.config.openshift.io','cluster')
        require(network['spec']['networkType']=='OVNKubernetes' and network.get('status',{}).get('networkType')=='OVNKubernetes',
                'This workflow requires active OVN-Kubernetes; it does not replace the CNI.')
        require(not any(c['type']=='NetworkDiagnosticsAvailable' and c['status']=='False' for c in network.get('status',{}).get('conditions',[])),
                'Network diagnostics are unavailable.')
        nodes=self.read(peer,'get','nodes')['items']
        require(bool(nodes),'No cluster nodes found.')
        for node in nodes:
            require(any(c['type']=='Ready' and c['status']=='True' for c in node['status']['conditions']),'All nodes must be Ready.')
        prefix='SUBMARINER_PEER' if peer else 'SUBMARINER_TARGET'
        name=self.env.get(prefix+'_GATEWAY','')
        candidates=[n for n in nodes if n['metadata']['name']==name]
        require(len(candidates)==1,'Select one exact gateway worker node in the private config.')
        node=candidates[0]
        labels=node['metadata'].get('labels',{})
        require(not any(k in labels for k in ('node-role.kubernetes.io/control-plane','node-role.kubernetes.io/master')),
                'A control-plane node cannot be selected as gateway in this lab workflow. Add a London worker first.')
        require('node-role.kubernetes.io/worker' in labels and labels.get('kubernetes.io/os')=='linux','Selected gateway must be a Linux worker node.')
        require(not node['spec'].get('unschedulable') and not node['spec'].get('taints'),'Gateway worker must be schedulable and untainted.')
        existing=[n['metadata']['name'] for n in nodes if n['metadata'].get('labels',{}).get('submariner.io/gateway')=='true']
        require(not existing or existing==[name],'Unexpected existing gateway labels; review manually instead of relabeling other nodes.')
        uid=self.read(peer,'get','namespace','kube-system')['metadata']['uid']
        ranges=network['spec'].get('clusterNetwork',[])
        cidrs=[x['cidr'] for x in ranges]+network['spec'].get('serviceNetwork',[])
        machine=self.env.get('SUBMARINER_PEER_MACHINE_CIDR' if peer else 'MACHINE_CIDR','')
        require(bool(machine),'Configure the actual target/peer machine subnet CIDR.')
        cidrs.append(machine)
        parsed=[ipaddress.ip_network(x) for x in cidrs]
        require(not any(a.overlaps(b) for i,a in enumerate(parsed) for b in parsed[i+1:]),'Machine, pod and service networks overlap within this cluster.')
        address=next((a['address'] for a in node['status'].get('addresses',[]) if a['type']=='InternalIP'),None)
        require(address is not None and ipaddress.ip_address(address) in parsed[-1],'Gateway IP does not belong to its configured machine subnet.')
        # Include machine ranges so DRG/site plans cannot overlap pod/service ranges.
        install=self.read(peer,'get','infrastructure.config.openshift.io','cluster')
        require(install.get('status',{}).get('controlPlaneTopology')=='SingleReplica','This first-cut workflow is for the single-control-plane lab.')
        return dict(uid=uid,cidrs=cidrs,gateway=name,node_uid=node['metadata']['uid'],
                    node_resource_version=node['metadata']['resourceVersion'],id=self.env.get(prefix+'_ID',''))

    def validation(self):
        target=self.health()
        require(bool(re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',target['id'])),'Set a valid target cluster ID.')
        result={'target':target}
        if self.enabled('SUBMARINER_ENABLE_PEER'):
            peer=self.health(True)
            require(peer['uid']!=target['uid'],'Both selections point to the same Kubernetes cluster.')
            require(peer['id']!=target['id'] and bool(re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',peer['id'])),'Cluster IDs must be valid and unique.')
            ensure_disjoint(target['cidrs'],peer['cidrs'])
            result['peer']=peer
        path=self.state/'clusters.json'
        old={}
        if path.exists():
            old=json.loads(path.read_text())
            for role,item in result.items():
                if role in old:
                    require(old[role]['uid']==item['uid'] and old[role]['id']==item['id'],'Cluster identity differs from saved networking state.')
        private_write(path,{**old,**result})
        return result

    def sub(self, args, peer=False, both=False):
        require(self.bin.is_file(),'Run 01-install-subctl.sh first.')
        _,path,context=self.selection(peer)
        flags=['--kubeconfig',str(path),'--context',context]
        if both:
            _,other,other_context=self.selection(True)
            flags+=['--toconfig',str(other),'--tocontext',other_context]
        require(self.version in run([str(self.bin),'version']),'Installed subctl differs from selected version; run step 01.')
        # Keep broker credentials and all generated verification/gather output private.
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.state,0o700)
        result=subprocess.run([str(self.bin),*args,*flags],cwd=self.state,stdin=subprocess.DEVNULL,check=True)
        return result

    def label(self, peer=False):
        info=self.health(peer)
        base,_,_=self.selection(peer)
        node=self.read(peer,'get','node',info['gateway'])
        require(node['metadata']['uid']==info['node_uid'],'Gateway identity changed before labeling.')
        patch=[{'op':'test','path':'/metadata/uid','value':info['node_uid']},
               {'op':'test','path':'/metadata/resourceVersion','value':info['node_resource_version']},
               {'op':'add','path':'/metadata/labels/submariner.io~1gateway','value':'true'}]
        run(base+['patch','node',info['gateway'],'--type=json','-p',json.dumps(patch)])

    def download(self):
        require(sys.platform.startswith('linux'),'Run Submariner scripts on the Linux admin host.')
        arch={'x86_64':'amd64','aarch64':'arm64'}.get(os.uname().machine)
        require(arch is not None,'Unsupported administration CPU architecture.')
        asset=f'subctl-v{self.version}-linux-{arch}.tar.gz'
        base=f'https://github.com/submariner-io/releases/releases/download/v{self.version}/'
        with urllib.request.urlopen(base+'subctl-checksums.txt',timeout=60) as response:
            lines=response.read().decode().splitlines()
        hashes=[line.split()[0] for line in lines if len(line.split())==2 and line.split()[1].lstrip('*')==asset]
        require(len(hashes)==1 and bool(re.fullmatch('[a-fA-F0-9]{64}',hashes[0])),'Missing or ambiguous official release checksum.')
        with urllib.request.urlopen(base+asset,timeout=120) as response:
            data=response.read()
        require(hashlib.sha256(data).hexdigest()==hashes[0].lower(),'subctl checksum mismatch.')
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
            members=[m for m in archive.getmembers() if Path(m.name).name=='subctl' and m.isfile()]
            require(len(members)==1,'Archive must contain exactly one subctl executable.')
            binary=archive.extractfile(members[0]).read()
        self.bin.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=self.bin.with_name('subctl.tmp-'+secrets.token_hex(4))
        with temp.open('xb') as stream: stream.write(binary)
        temp.chmod(0o700)
        output=run([str(temp),'version'])
        require(self.version in output,'Downloaded binary version differs from the selected release.')
        temp.replace(self.bin)
        print('Pinned subctl installed privately; no cluster changes made.')

    def execute(self, action):
        os.umask(0o077)
        if action=='initialize': return self.initialize()
        if action=='download': return self.download()
        if action=='review': return self.review_local()
        if action in ('peer','verify'):
            self.peer_allowed()
            require(self.enabled('SUBMARINER_PEER_NETWORK_REVIEWED'),'Review and record peer gateway routes/ports/MTU before enrollment or traffic tests.')
        if action in ('status','gather'):
            saved=json.loads((self.state/'clusters.json').read_text())
            for peer in ([False,True] if self.enabled('SUBMARINER_ENABLE_PEER') else [False]):
                if peer: self.peer_allowed()
                role='peer' if peer else 'target'
                uid=self.read(peer,'get','namespace','kube-system')['metadata']['uid']
                require(role in saved and saved[role]['uid']==uid,'Cluster identity differs from saved networking state.')
            info={k:v for k,v in saved.items() if k=='target' or self.enabled('SUBMARINER_ENABLE_PEER')}
        else:
            info=self.validation()
        if action=='validate':
            print('PASS: London health, OVN and gateway checks. Peer is '+('validated.' if 'peer' in info else 'disabled; no cross-site connectivity is claimed.'))
        elif action=='label': self.label()
        elif action=='broker':
            if self.broker.exists():
                require((self.state/'broker-owner.json').exists(),'Existing broker credentials have no ownership receipt; preserve and review manually.')
                require(json.loads((self.state/'broker-owner.json').read_text())['uid']==info['target']['uid'],'Broker belongs to a different cluster.')
                print('Existing London broker credentials retained.');return
            base,_,_=self.selection()
            existing=run(base+['get','namespace','submariner-k8s-broker','--ignore-not-found','-o','name']).strip()
            require(not existing,'Broker namespace already exists without saved credentials. Recover/review manually; do not recreate it.')
            self.sub(['deploy-broker','--version',self.version,'--globalnet=false','--components','connectivity,service-discovery'])
            require(self.broker.is_file(),'Broker command did not generate private credentials.')
            self.broker.chmod(0o600)
            private_write(self.state/'broker-owner.json',{'uid':info['target']['uid']})
        elif action in ('join','peer'):
            peer=action=='peer'
            if peer: self.peer_allowed()
            require(self.broker.is_file() and (self.state/'broker-owner.json').is_file(),'Prepare London broker first; retain its credential file.')
            require(json.loads((self.state/'broker-owner.json').read_text())['uid']==info['target']['uid'],'Broker credentials belong to a different London cluster.')
            # Refuse automatic upgrades/re-joins of an existing network deployment.
            base,_,_=self.selection(peer)
            exists=run(base+['get','namespace','submariner-operator','--ignore-not-found','-o','name']).strip()
            require(not exists,'Submariner operator namespace already exists; inspect status and preserve it. This script does not upgrade or reinstall.')
            if peer: self.label(True)
            side=info['peer' if peer else 'target']
            node=self.read(peer,'get','node',side['gateway'])
            require(node['metadata'].get('labels',{}).get('submariner.io/gateway')=='true','Run the gateway-label step before joining.')
            self.sub(['join',str(self.broker),'--clusterid',side['id'],'--version',self.version,
                      '--cable-driver','libreswan','--label-gateway=false','--natt=false','--check-broker-certificate=true'],peer=peer)
        elif action=='status':
            self.sub(['show','all'])
            if 'peer' not in info: print('London-only stage: a remote tunnel is not expected until peer enrollment.')
        elif action=='verify':
            self.peer_allowed()
            require('peer' in info,'Enroll two distinct clusters before verification.')
            self.sub(['verify','--only','connectivity,service-discovery','--disruptive-tests=false','--verbose',
                      '--junit-report',str(self.state/'verify.xml')],both=True)
        elif action=='gather':
            self.sub(['gather'])
            if 'peer' in info: self.sub(['gather'],peer=True)
        else: raise ValueError('Unknown networking action')


def ensure_disjoint(first, second):
    a=[ipaddress.ip_network(x) for x in first]
    b=[ipaddress.ip_network(x) for x in second]
    require(not any(x.overlaps(y) for x in a for y in b),'Pod/service CIDRs overlap between clusters. This OVN workflow does not enable Globalnet.')


if __name__=='__main__':
    try:
        Workflow().execute(sys.argv[1])
    except (ValueError,KeyError,OSError,subprocess.CalledProcessError) as error:
        sys.exit('STOP: '+str(error))
