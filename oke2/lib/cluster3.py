#!/usr/bin/env python3
"""Cross-OKE native ClusterSet replica with explicit per-cluster API routing."""
import argparse
import base64
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'lib'))
import cluster_config
import clusterset
import mysqlsh_pod

class Error(Exception):
    pass

def need(ok, message):
    if not ok:
        raise Error(message)

def k(which, *args, payload=None, obj=False, timeout=60):
    cmd = ['kubectl', '--kubeconfig', os.environ[f'C{which}_KUBECONFIG'],
           '--context', os.environ[f'C{which}_CONTEXT'], '--request-timeout=30s', *args]
    proc = subprocess.run(cmd, input=payload, text=True, capture_output=True, timeout=timeout)
    need(proc.returncode == 0, proc.stderr.strip() or 'kubectl failed')
    return json.loads(proc.stdout) if obj and proc.stdout.strip() else proc.stdout.strip()

def get(which, kind, name=None, ns=None, optional=False):
    return k(which, *(['-n', ns] if ns else []), 'get', kind, *([name] if name else []),
             *(['--ignore-not-found'] if optional else []), '-o', 'json', obj=True)

def config():
    for key in ('CLUSTER3_NAMESPACE','CLUSTER3_NAME','CLUSTER3_SECRET','C1_SOURCE_NAMESPACE','C1_SOURCE_CLUSTER'):
        need(re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', os.environ[key]) and len(os.environ[key]) <= 40,
             f'Invalid {key}')
    need(os.environ['CLUSTER3_NAMESPACE'] not in ('default','kube-system','kube-public','kube-node-lease',os.environ['OPERATOR_NAMESPACE']), 'Use a dedicated cluster3 namespace.')
    need(re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', os.environ['K8S_CLUSTER_DOMAIN']), 'Invalid Kubernetes DNS domain.')
    for key, low, high in [('CLUSTER3_BASE_SERVER_ID',1,4294967195),('CLUSTER3_INSTANCES',1,9),('CLUSTER3_ROUTERS',1,9)]:
        need(os.environ[key].isdigit() and low <= int(os.environ[key]) <= high, f'Invalid {key}')

def identity():
    config()
    identities=[]
    for which in (1,2):
        view=k(which,'config','view','--minify','-o','json',obj=True)
        identities.append(view['clusters'][0]['cluster']['server'])
        k(which,'get','--raw=/readyz')
    need(identities[0] != identities[1], 'c1 and c2 point at the same API server; refusing cross-cluster setup.')
    print('C1:',os.environ['C1_CONTEXT'],identities[0],flush=True)
    print('C2:',os.environ['C2_CONTEXT'],identities[1],flush=True)

def credentials(cluster):
    secret=get(1,'secret',cluster['spec']['secretName'],cluster['metadata']['namespace'])
    data={key:secret.get('data',{}).get(key) for key in ('rootUser','rootHost','rootPassword')}
    need(all(data.values()),'Source root Secret is incomplete.')
    need(base64.b64decode(data['rootUser'])==b'root' and base64.b64decode(data['rootHost'])==b'%', 'Requires source rootUser=root and rootHost=%.')
    need(not any(c in base64.b64decode(data['rootPassword']) for c in (b'\n',b'\r',b'\0')), 'Unsupported password characters.')
    return data

def shell(which, cluster, data, body, pod=None):
    output=k(which,'--request-timeout=0','-n',cluster['metadata']['namespace'],'exec','-i',pod or cluster['metadata']['name']+'-0','-c','sidecar','--',
             'mysqlsh','--uri','root@127.0.0.1:3306','--passwords-from-stdin','--quiet-start=2','--py','-e',
             'import json\n'+body,payload=base64.b64decode(data['rootPassword']).decode()+'\n',timeout=120)
    marker='CLUSTER3_JSON='
    need(marker in output,'MySQL Shell returned no structured result.')
    return json.JSONDecoder().raw_decode(output.split(marker,1)[1])[0]

def status(which, cluster, data):
    return shell(which,cluster,data,"c=dba.get_cluster(); s=c.get_cluster_set(); print('CLUSTER3_JSON='+json.dumps({'name':c.name,'status':s.status({'extended':1}),'readOnly':shell.get_session().run_sql('SELECT @@global.super_read_only').fetch_one()[0]}))")

def source(healthy=True):
    anchor=get(1,'innodbcluster',os.environ['C1_SOURCE_CLUSTER'],os.environ['C1_SOURCE_NAMESPACE'])
    data=credentials(anchor); live=status(1,anchor,data)['status']
    if healthy:
        need(live.get('status')=='HEALTHY','Existing ClusterSet must be HEALTHY before adding cluster3.')
    name=live['primaryCluster']
    choices=k(1,'get','innodbclusters','-A','-o','json',obj=True)['items']
    primary=next((c for c in choices if c['metadata']['name'].replace('-','_')==name),None)
    need(primary is not None, 'Current ClusterSet primary is not in c1; configure the source explicitly for that topology.')
    need(primary['spec'].get('tlsUseSelfSigned') is True, 'This starter setup requires self-signed TLS; configure managed certificates separately.')
    need(credentials(primary)==data, 'Source cluster credentials differ from the anchor.')
    return primary,data,live,choices

def ready_dns(which):
    explicit=os.environ[f'C{which}_DNS_SERVERS'].split()
    if explicit:
        ips=explicit
    else:
        pods=k(which,'-n','kube-system','get','pods','-l','k8s-app=kube-dns','-o','json',obj=True)['items']
        ips=[p['status']['podIP'] for p in pods if not p['metadata'].get('deletionTimestamp') and any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[]))]
    need(ips, f'No Ready CoreDNS IPs found in c{which}.')
    for ip in ips:
        need(ipaddress.ip_address(ip).version==4, 'This lab forwarding configuration requires IPv4 DNS IPs.')
    need(len(ips)<=15,'CoreDNS forward supports at most 15 upstreams; configure stable DNS resolvers.')
    return sorted(set(ips))

def forwarding(zones, ips):
    return '\n'.join(f'{zone}:53 {{\n    errors\n    cache 5\n    forward . {" ".join(ips)}\n}}\n' for zone in sorted(zones))

def dns_plan(clusters):
    rn=os.environ['CLUSTER3_NAMESPACE'];domain=os.environ['K8S_CLUSTER_DOMAIN']
    namespaces={c['metadata']['namespace'] for c in clusters}
    need(rn not in namespaces, 'cluster3 namespace must be distinct from every c1 database namespace.')
    for ns in namespaces:
        services=get(2,'services',ns=ns,optional=True)
        need(not services or not services.get('items'),f'c2 already has services in {ns}; forwarding would shadow them.')
    need(not get(1,'namespace',rn,optional=True),f'c1 already has namespace {rn}; remote namespace forwarding would shadow local DNS.')
    plans={1:forwarding({f'{rn}.svc.{domain}'},ready_dns(2)),
           2:forwarding({f'{ns}.svc.{domain}' for ns in namespaces},ready_dns(1))}
    for which in (1,2):
        cm=get(which,'configmap','coredns','kube-system')
        corefile=cm.get('data',{}).get('Corefile','')
        need('import custom/*.server' in corefile, f'c{which} CoreDNS does not import custom/*.server.')
        need(re.search(r'\bkubernetes\s+'+re.escape(domain)+r'\s',corefile), f'c{which} has a different Kubernetes DNS domain.')
        need(re.search(r'^\s*reload\b',corefile,re.M), f'c{which} CoreDNS lacks automatic reload; configure it before using this workflow.')
        deployment=get(which,'deployment','coredns','kube-system')
        need(any(v.get('configMap',{}).get('name')=='coredns-custom' for v in deployment['spec']['template']['spec']['volumes']), 'CoreDNS does not mount coredns-custom.')
        old=get(which,'configmap','coredns-custom','kube-system',optional=True)
        for key,value in (old or {}).get('data',{}).items():
            if key=='mysqlk8s-clusterset.server':continue
            need(not any(zone in value for zone in re.findall(r'([a-z0-9.-]+):53',plans[which])),f'Conflicting DNS customization in {key}; resolve manually.')
    return plans

def save_backup(which, old):
    directory=ROOT/'generated';directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/f'c{which}-coredns-custom-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}.json'
    path.write_text(json.dumps(old,indent=2)+'\n');path.chmod(0o600)
    print('DNS backup:',path)

def configure_dns(dry_run=False):
    identity(); _,_,_,clusters=source();plans=dns_plan(clusters)
    for which,text in plans.items():
        print(f'c{which} mysqlk8s-clusterset.server:\n{text}',flush=True)
    if dry_run:return
    for which,text in plans.items():
        old=get(which,'configmap','coredns-custom','kube-system',optional=True)
        if (old or {}).get('data',{}).get('mysqlk8s-clusterset.server')==text:continue
        save_backup(which,old)
        if old:
            # Optimistic replace preserves every other key and rejects concurrent edits.
            old.setdefault('data',{})['mysqlk8s-clusterset.server']=text
            k(which,'replace','-f','-',payload=json.dumps(old))
        else:
            k(which,'create','-f','-',payload=json.dumps({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'coredns-custom','namespace':'kube-system'},'data':{'mysqlk8s-clusterset.server':text}}))
    print('CoreDNS reloads projected configuration automatically; allow up to a few minutes. No CoreDNS restart: upstream pod IPs remain valid. Rerun step 05 after remote CoreDNS IP changes.',flush=True)


def build_manifest(primary, target):
    saved=dict(os.environ)
    try:
        os.environ.update(MYSQL_NAMESPACE=os.environ['CLUSTER3_NAMESPACE'],MYSQL_CLUSTER_NAME=os.environ['CLUSTER3_NAME'],MYSQL_SECRET_NAME=os.environ['CLUSTER3_SECRET'],MYSQL_INSTANCES=os.environ['CLUSTER3_INSTANCES'],MYSQL_ROUTER_INSTANCES=os.environ['CLUSTER3_ROUTERS'],MYSQL_VERSION=primary['spec']['version'],MYSQL_STORAGE_SIZE=os.environ['CLUSTER3_STORAGE_SIZE'],STORAGE_CLASS=os.environ['CLUSTER3_STORAGE_CLASS'])
        cluster_config.validate();doc=cluster_config.build_manifest()
    finally:
        os.environ.clear();os.environ.update(saved)
    doc['spec']['edition']=primary['spec'].get('edition','community')
    doc['spec']['baseServerId']=int(os.environ['CLUSTER3_BASE_SERVER_ID'])
    doc['spec']['initDB']={'clusterSet':{'targetUrl':target,'rootUser':'root','secretKeyRef':{'name':os.environ['CLUSTER3_SECRET']}}}
    return doc

def check_ids(doc, clusters):
    base=doc['spec']['baseServerId']
    for c in clusters:
        if c['metadata']==doc['metadata']:continue
        starts=[int(c['spec'].get('baseServerId',1000))]+[int(r['baseServerId']) for r in c['spec'].get('readReplicas',[])]
        need(all(base+99 < b or b+99 < base for b in starts), f'Server ID block overlaps {c["metadata"]["name"]}.')

def member_targets(live):
    targets={}
    for member in live['clusters'].values():
        for endpoint,details in member.get('topology',{}).items():
            need(details.get('status')=='ONLINE',f'ClusterSet member {endpoint} is not ONLINE.')
            targets[endpoint]=None
    need(targets,'ClusterSet has no member endpoints.')
    return targets

def probe(which, ns, pod, targets, timeout=60):
    code='import socket,json\nchecks='+repr(targets)+'\n'
    code+="for endpoint,expected in checks.items():\n h,p=endpoint.rsplit(':',1); actual=socket.gethostbyname(h)\n if expected and actual!=expected: raise RuntimeError('DNS points to wrong pod: '+h+' '+actual)\n with socket.create_connection((h,int(p)),5): pass\n print(h+' -> '+actual+' TCP/'+p+' OK')\n"
    return k(which,'--request-timeout=0','-n',ns,'exec',pod,'--','mysqlsh','--py','-e',code,timeout=timeout)

def temporary_probe(primary, targets):
    source_pod=get(1,'pod',primary['metadata']['name']+'-0',primary['metadata']['namespace'])
    ns=os.environ['OPERATOR_NAMESPACE'];name='cluster3-check-'+uuid.uuid4().hex[:10]
    pod=mysqlsh_pod.build_pod(source_pod,ns,name)
    k(2,'create','-f','-',payload=json.dumps(pod))
    try:
        k(2,'--request-timeout=0','-n',ns,'wait','--for=condition=Ready','pod/'+name,'--timeout=180s',timeout=200)
        deadline=time.monotonic()+180
        while True:
            try:
                print(probe(2,ns,name,targets),flush=True);return
            except Error:
                if time.monotonic()>=deadline:raise
                time.sleep(10)
    finally:
        k(2,'-n',ns,'delete','pod',name,'--ignore-not-found','--wait=false')

def create(dry_run=False):
    identity();primary,data,live,clusters=source()
    rn,rc=os.environ['CLUSTER3_NAMESPACE'],os.environ['CLUSTER3_NAME'];secret=os.environ['CLUSTER3_SECRET']
    target=live['clusters'][live['primaryCluster']]['primary']
    doc=build_manifest(primary,target)
    get(2,'storageclass',os.environ['CLUSTER3_STORAGE_CLASS'])
    crd=get(2,'crd','innodbclusters.mysql.oracle.com')
    schema=next(v['schema']['openAPIV3Schema'] for v in crd['spec']['versions'] if v['name']=='v2')
    need('clusterSet' in schema['properties']['spec']['properties']['initDB']['properties'],'c2 operator lacks native ClusterSet bootstrap support.')
    operator=get(2,'deployment','mysql-operator',os.environ['OPERATOR_NAMESPACE'])
    need(operator.get('status',{}).get('availableReplicas',0)>0,'Install the c2 MySQL Operator with step 04 first.')
    scopes=next((e.get('value','') for e in operator['spec']['template']['spec']['containers'][0].get('env',[]) if e['name']=='OPERATOR_NAMESPACES'),'')
    need(not scopes or rn in scopes.split(','),'c2 operator does not watch the cluster3 namespace.')
    saved=dict(os.environ)
    try:
        os.environ['REPLICA_MYSQL_INSTANCES']=os.environ['CLUSTER3_INSTANCES']
        clusterset.check_workers(get(2,'nodes'))
    finally:
        os.environ.clear();os.environ.update(saved)
    dest=k(2,'get','innodbclusters','-A','-o','json',obj=True)['items']
    check_ids(doc,clusters+[c for c in dest if (c['metadata']['name'],c['metadata']['namespace'])!=(rc,rn)])
    old=get(2,'innodbcluster',rc,rn,optional=True) if get(2,'namespace',rn,optional=True) else None
    if old:
        # targetUrl records the original bootstrap member and can change after switchovers.
        requested=json.loads(json.dumps(doc));requested['spec']['initDB']=old['spec'].get('initDB')
        need(old['spec'].get('initDB',{}).get('clusterSet',{}).get('secretKeyRef',{}).get('name')==secret,'Existing cluster is not this ClusterSet bootstrap.')
        clusterset.validate_existing(old,requested)
        saved_secret=get(2,'secret',secret,rn,optional=True)
        need(saved_secret and saved_secret.get('data')==data,'Existing cluster3 credentials differ; refusing overwrite.')
        local=status(2,old,data)
        need(local['status']['domainName']==live['domainName'],'Existing cluster3 belongs to another ClusterSet.')
        print('Matching cluster3 already exists; no resources changed. Run step 07.');return
    need(rc.replace('-','_') not in live['clusters'],'ClusterSet metadata already contains this name; investigate before recreating.')
    plans=dns_plan(clusters)
    for which,text in plans.items():
        cm=get(which,'configmap','coredns-custom','kube-system',optional=True)
        need(cm and cm.get('data',{}).get('mysqlk8s-clusterset.server')==text,'DNS forwarding is missing/stale; run step 05.')
    targets=member_targets(live)
    # Match DNS answers against live Kubernetes pod addresses, not only any open port.
    for c in clusters:
        ns,name=c['metadata']['namespace'],c['metadata']['name']
        for p in get(1,'pods',ns=ns)['items']:
            ep=f'{p["metadata"]["name"]}.{name}-instances.{ns}.svc.{os.environ["K8S_CLUSTER_DOMAIN"]}:3306'
            if ep in targets:targets[ep]=p['status']['podIP']
    need(all(targets.values()),'Live advertised endpoints do not match default member FQDNs; customize DNS for this topology.')
    print(json.dumps(doc,indent=2),flush=True)
    if dry_run:
        print('Preview only: read-only checks; no probe pod, Secret or database resources created.');return
    temporary_probe(primary,targets)
    if not get(2,'namespace',rn,optional=True):k(2,'create','namespace',rn)
    need(not any(p['metadata']['name'].startswith('datadir-'+rc+'-') for p in get(2,'pvc',ns=rn)['items']), 'Orphan database PVCs exist; use a fresh name/namespace.')
    need(not get(2,'statefulset',rc,rn,optional=True),'Orphan StatefulSet exists.')
    rendered=json.dumps(doc,indent=2)
    k(2,'apply','--dry-run=server','-f','-',payload=rendered)
    existing=get(2,'secret',secret,rn,optional=True)
    if existing:need(existing.get('data')==data,'Existing Secret differs; refusing overwrite.')
    else:k(2,'create','-f','-',payload=json.dumps({'apiVersion':'v1','kind':'Secret','metadata':{'name':secret,'namespace':rn},'type':'Opaque','data':data}))
    k(2,'create','-f','-',payload=rendered)
    print('cluster3 bootstrap submitted to OKE c2. Run 07-verify-cluster3.sh.',flush=True)

def verify():
    identity();rn,rc=os.environ['CLUSTER3_NAMESPACE'],os.environ['CLUSTER3_NAME']
    deadline=time.monotonic()+int(os.environ['WAIT_TIMEOUT_SECONDS'])
    while True:
        replica=get(2,'innodbcluster',rc,rn);health=replica.get('status',{}).get('cluster',{})
        print('cluster3:',health.get('status','PENDING'),f'{health.get("onlineInstances",0)}/{os.environ["CLUSTER3_INSTANCES"]}',flush=True)
        if health.get('status')=='ONLINE' and health.get('onlineInstances')==int(os.environ['CLUSTER3_INSTANCES']):break
        need(time.monotonic()<deadline,'Timed out waiting for cluster3; inspect c2 operator/sidecar logs and events.')
        time.sleep(10)
    for resource in ('statefulset/'+rc,'deployment/'+rc+'-router'):
        k(2,'--request-timeout=0','-n',rn,'rollout','status',resource,'--timeout='+os.environ['WAIT_TIMEOUT_SECONDS']+'s',timeout=int(os.environ['WAIT_TIMEOUT_SECONDS'])+30)
    while True:
        primary,data,live,_=source(healthy=False)
        member=live.get('clusters',{}).get(rc.replace('-','_'),{})
        if live.get('status')=='HEALTHY' and member.get('clusterRole')=='REPLICA' and member.get('globalStatus')=='OK':break
        print('Waiting for healthy ClusterSet replica:',live.get('status'),member.get('globalStatus'),flush=True)
        need(time.monotonic()<deadline,'cluster3 did not converge to a healthy ClusterSet REPLICA before the timeout.')
        time.sleep(10)
    local=status(2,replica,data)
    need(local['status']['domainName']==live['domainName'] and local['readOnly']==1,'cluster3 ClusterSet identity/read-only state differs.')
    # Exercise reverse DNS/TCP from c1 after c3 members exist, including c1 replica sidecars.
    targets=member_targets({'clusters':{rc:member}})
    for endpoint in targets:
        ordinal=endpoint.split('.',1)[0]
        targets[endpoint]=get(2,'pod',ordinal,rn)['status']['podIP']
    source_pods=k(1,'get','pods','-A','-l','component=mysqld','-o','json',obj=True)['items']
    # exec the known sidecar container (probe() selects the standalone mysqlsh probe by default).
    for pod in source_pods:
        if any(c['name']=='sidecar' for c in pod['spec']['containers']):
            code='import socket\nchecks='+repr(targets)+"\nfor ep,expected in checks.items():\n h,p=ep.rsplit(':',1);actual=socket.gethostbyname(h)\n if actual!=expected: raise RuntimeError('Incorrect remote DNS: '+h)\n with socket.create_connection((h,int(p)),5):pass\n print(ep+' -> '+actual+' OK')\n"
            print(k(1,'--request-timeout=0','-n',pod['metadata']['namespace'],'exec',pod['metadata']['name'],'-c','sidecar','--','mysqlsh','--py','-e',code),flush=True)
    receiver=member['primary'].split('.',1)[0]
    query="SELECT c.HOST,c.PORT,s.SERVICE_STATE,s.SOURCE_UUID,s.LAST_ERROR_NUMBER,s.LAST_ERROR_MESSAGE,a.SERVICE_STATE FROM performance_schema.replication_connection_configuration c LEFT JOIN performance_schema.replication_connection_status s USING (CHANNEL_NAME) LEFT JOIN performance_schema.replication_applier_status a USING (CHANNEL_NAME) WHERE c.CHANNEL_NAME='clusterset_replication'"
    channels=shell(2,replica,data,"rows=shell.get_session().run_sql("+repr(query)+").fetch_all(); print('CLUSTER3_JSON='+json.dumps([list(r) for r in rows]))",pod=receiver)
    need(len(channels)==1 and channels[0][2]=='ON' and channels[0][4]==0 and channels[0][6]=='ON', 'cluster3 replication receiver/applier is not running cleanly: '+json.dumps(channels))
    print(json.dumps(live,indent=2))
    print('cluster3 receiver:',receiver,'configured source:',str(channels[0][0])+':'+str(channels[0][1]),'connected source UUID:',channels[0][3],'IO=ON SQL=ON')
    print('Verified cluster3 in OKE c2: ONLINE, ClusterSet REPLICA / OK, super_read_only=1.',flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['identity','dns','create','verify']);parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    try:
        if args.action=='identity':identity()
        elif args.action=='dns':configure_dns(args.dry_run)
        elif args.action=='create':create(args.dry_run)
        else:verify()
    except (Error,clusterset.SetupError,subprocess.TimeoutExpired,ValueError,KeyError) as error:
        sys.exit('ERROR: '+str(error))
if __name__=='__main__':main()
