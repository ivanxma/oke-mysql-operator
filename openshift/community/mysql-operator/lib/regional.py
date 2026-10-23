"""Worker-only regional MySQL Operator/ClusterSet deployment over Submariner."""
import base64
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'openshift/mysql-operator/lib'))
import runtime as rt
cs=rt.cs
OWNER='okd-regional-mysql'
rt.OWNER=OWNER

def require(value,message):
 if not value:raise ValueError(message)
def oc(*args,payload=None,json_output=False,timeout=45):
 command=['oc','--kubeconfig',os.environ['KUBECONFIG'],'--context',os.environ['KUBE_CONTEXT'],'--request-timeout=30s',*args]
 result=subprocess.run(command,input=payload,text=True,capture_output=True,timeout=timeout)
 require(result.returncode==0,result.stderr.strip() or 'OpenShift request failed.')
 return json.loads(result.stdout) if json_output and result.stdout.strip() else (None if json_output else result.stdout.strip())
cs.kubectl=oc

def get(kind,name=None,namespace=None,optional=False):return cs.get(kind,name,namespace,optional)
def guard():
 rt.validate()
 path=Path(os.environ['KUBECONFIG'])
 require(path.is_absolute() and path.is_file() and path.resolve()!=(Path.home()/'.kube/config').resolve(),'Use the selected explicit OKD KUBECONFIG.')
 require(oc('config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}')==os.environ['MYSQL_EXPECTED_API_SERVER'],'Wrong regional API.')
 require(get('namespace','kube-system')['metadata']['uid']==os.environ['MYSQL_OKD_UID'],'OKD cluster identity changed.')
 require(os.environ['MYSQL_SITE_ROLE'] in ('PRIMARY','REPLICA'),'Select a generated site role.')
 require(Path(os.environ['MYSQL_STATE_DIR']).resolve().parent==Path(os.environ['MYSQL_REGIONAL_CONFIG']).resolve().parent,'Keep state beside the generated regional configuration.')
rt.guard=guard

def state_file(name,document):
 p=Path(os.environ['MYSQLK8S_GENERATED_DIR'])/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 p.write_text(json.dumps(document,indent=2)+'\n');p.chmod(0o600);return p

def own_apply(document):rt.apply(document)
def credentials():
 p=Path(os.environ['MYSQL_ROOT_PASSWORD_FILE'])
 require(p.is_file() and not p.is_symlink() and not p.stat().st_mode & 0o077,'Protected generated password file required.')
 value=p.read_text().rstrip('\n');minimum=8 if os.environ.get('MYSQL_LAB_MODE')=='true' else 12
 require(len(value)>=minimum and not any(c in value for c in '\r\n\0'),'Password does not meet the selected lab/normal policy.')
 return {k:base64.b64encode(v.encode()).decode() for k,v in dict(rootUser='root',rootHost='%',rootPassword=value).items()}

def manifest():
 document=rt.build_manifest();spec=document['spec'];spec['baseServerId']=int(os.environ['MYSQL_BASE_SERVER_ID'])
 spec['serviceFqdnTemplate']=os.environ['MYSQL_SUBMARINER_ID']+'.{service}.{namespace}.svc.clusterset.local'
 return document

def security():
 namespaces=[os.environ['OPERATOR_NAMESPACE'],os.environ['MYSQL_NAMESPACE']]
 documents=[dict(apiVersion='v1',kind='Namespace',metadata=rt.metadata(n)) for n in namespaces]
 documents += [d for d in rt.security_documents() if d['metadata'].get('namespace') in (None,*namespaces)]
 for d in documents:rt.managed(d['kind'],d['metadata']['name'],d['metadata'].get('namespace'))
 for d in documents:own_apply(d)
 print('Prepared only this region operator/database namespaces and fixed-UID SCC grants; control plane excluded.',flush=True)

def operator_values():
 values=rt.operator_values();values['deployment']['namespaces']=[os.environ['MYSQL_NAMESPACE']];return values

def peer_env():
 region=os.environ['MYSQL_PEER_REGION'];selected=Path(os.environ['OKD_CONFIG_FILE']);peer=ROOT/'generated/openshift'/region/selected.parent.name/'mysql-operator/00-env.sh'
 require(peer.is_file(),'Generate the peer MySQL profile first.')
 code='import os,json; print(json.dumps({k:os.environ.get(k, "") for k in '+repr(('KUBECONFIG','KUBE_CONTEXT','MYSQL_NAMESPACE','MYSQL_CLUSTER_NAME','MYSQL_SECRET_NAME','MYSQL_SUBMARINER_ID','MYSQL_VERSION','MYSQL_SITE_ROLE','MYSQL_OKD_UID','MYSQL_EXPECTED_API_SERVER','MYSQL_ROOT_PASSWORD_FILE'))+'}))'
 return json.loads(subprocess.check_output(['bash','-c','set -e; source "$1"; python3 -c "$2"','--',str(peer),code],text=True,timeout=30))

def peer_get(values,kind,name,namespace=None):
 args=['oc','--kubeconfig',values['KUBECONFIG'],'--context',values['KUBE_CONTEXT'],'--request-timeout=20s']
 cfg=json.loads(subprocess.check_output(args+['config','view','--minify','-o','json'],text=True,timeout=30))
 require(cfg['clusters'][0]['cluster']['server']==values['MYSQL_EXPECTED_API_SERVER'],'Wrong peer API.')
 uid=json.loads(subprocess.check_output(args+['get','namespace','kube-system','-o','json'],text=True,timeout=30))['metadata']['uid']
 require(uid==values['MYSQL_OKD_UID'] and uid!=os.environ['MYSQL_OKD_UID'],'Peer cluster identity differs or selects this site.')
 return json.loads(subprocess.check_output(args+(['-n',namespace] if namespace else [])+['get',kind,name,'-o','json'],text=True,timeout=30))

def export_service():
 ns=os.environ['MYSQL_NAMESPACE'];name=os.environ['MYSQL_CLUSTER_NAME']+'-instances'
 own_apply(dict(apiVersion='multicluster.x-k8s.io/v1alpha1',kind='ServiceExport',metadata=rt.metadata(name,ns)))
 print('Exported cluster headless service for stable Submariner pod/metadata addresses.',flush=True)

def create_cluster():
 ns=os.environ['MYSQL_NAMESPACE'];name=os.environ['MYSQL_CLUSTER_NAME'];doc=manifest();data=credentials()
 rt.workers(get('nodes'));get('storageclass',os.environ['STORAGE_CLASS'])
 if os.environ['MYSQL_SITE_ROLE']=='REPLICA':
  peer=peer_env();require(peer['MYSQL_SITE_ROLE']=='PRIMARY' and peer['MYSQL_VERSION']==os.environ['MYSQL_VERSION'],'Replica requires the selected same-version London primary.')
  primary=peer_get(peer,'innodbcluster',peer['MYSQL_CLUSTER_NAME'],peer['MYSQL_NAMESPACE'])
  health=primary.get('status',{}).get('cluster',{});require(health.get('status')=='ONLINE' and health.get('onlineInstances')==primary['spec']['instances'],'London primary must be ONLINE before replica bootstrap.')
  peer_secret=peer_get(peer,'secret',peer['MYSQL_SECRET_NAME'],peer['MYSQL_NAMESPACE']);require(peer_secret['data']==data,'Replica and primary configured root credentials differ; no secret overwrite.')
  endpoint=peer['MYSQL_CLUSTER_NAME']+'-0.'+peer['MYSQL_SUBMARINER_ID']+'.'+peer['MYSQL_CLUSTER_NAME']+'-instances.'+peer['MYSQL_NAMESPACE']+'.svc.clusterset.local:3306'
  doc['spec']['initDB']=dict(clusterSet=dict(targetUrl=endpoint,rootUser='root',secretKeyRef=dict(name=os.environ['MYSQL_SECRET_NAME'])))
 existing=rt.managed('innodbcluster',name,ns)
 if existing:
  require(all(rt.includes(existing['spec'].get(k),v) for k,v in doc['spec'].items()),'Existing regional cluster configuration differs; no upgrade/adoption.')
  secret=get('secret',os.environ['MYSQL_SECRET_NAME'],ns);require(secret['data']==data,'Existing database credentials differ; no rotation.')
  print('Existing matching regional InnoDBCluster retained.',flush=True);return
 require(not get('statefulset',name,ns,True),'Orphan StatefulSet; use a new cluster identity.')
 require(not any(p['metadata']['name'].startswith('datadir-'+name+'-') for p in get('pvc',namespace=ns)['items']),'Orphan datadir PVC; refusing reuse.')
 oc('-n',ns,'apply','--dry-run=server','-f','-',payload=json.dumps(doc))
 secret=get('secret',os.environ['MYSQL_SECRET_NAME'],ns,True)
 if secret:require(secret['data']==data,'Existing secret differs; no overwrite.')
 else:
  secret=dict(apiVersion='v1',kind='Secret',metadata=rt.metadata(os.environ['MYSQL_SECRET_NAME'],ns),type='Opaque',data=data)
  oc('create','-f','-',payload=json.dumps(secret))
 state_file('innodbcluster.json',doc);oc('create','-f','-',payload=json.dumps(doc))
 print('Submitted '+name+' '+os.environ['MYSQL_SITE_ROLE']+'; credentials stayed in protected files and Secret stdin.',flush=True)

def wait_cluster():
 ns=os.environ['MYSQL_NAMESPACE'];name=os.environ['MYSQL_CLUSTER_NAME'];deadline=time.monotonic()+int(os.environ['WAIT_TIMEOUT_SECONDS'])
 while True:
  cr=get('innodbcluster',name,ns);s=cr.get('status',{}).get('cluster',{})
  print(name+': '+s.get('status','PENDING')+'; ONLINE '+str(s.get('onlineInstances',0))+'/'+os.environ['MYSQL_INSTANCES'],flush=True)
  if s.get('status')=='ONLINE' and s.get('onlineInstances')==int(os.environ['MYSQL_INSTANCES']):break
  # Surface active storage failures without deleting a claim or credential.
  rt.storage_errors(ns,name)
  require(time.monotonic()<deadline,'Cluster wait timed out. Inspect events/operator/clone status; do not recreate data.')
  time.sleep(15)
 for resource in ['statefulset/'+name,'deployment/'+name+'-router']:
  oc('--request-timeout=0','-n',ns,'rollout','status',resource,'--timeout='+os.environ['WAIT_TIMEOUT_SECONDS']+'s',timeout=int(os.environ['WAIT_TIMEOUT_SECONDS'])+30)
 pods=get('pods',namespace=ns)['items'];nodes={n['metadata']['name']:n for n in get('nodes')['items']}
 for pod in pods:
  if pod['metadata'].get('labels',{}).get('mysql.oracle.com/cluster')!=name:continue
  node=nodes[pod['spec']['nodeName']];labels=node['metadata']['labels']
  require(labels.get('workload')=='mysql-cluster' and not any(k in labels for k in ('node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane')),'Database/Router landed outside workers.')
 print('ONLINE with worker-only placement.',flush=True)

def sql(code,values=None):
 ns=values['MYSQL_NAMESPACE'] if values else os.environ['MYSQL_NAMESPACE'];name=values['MYSQL_CLUSTER_NAME'] if values else os.environ['MYSQL_CLUSTER_NAME']
 password_file=values['MYSQL_ROOT_PASSWORD_FILE'] if values else os.environ['MYSQL_ROOT_PASSWORD_FILE']
 path=Path(password_file);require(path.is_file() and not path.stat().st_mode & 0o077,'Protected SQL credentials required.')
 args=['oc','--kubeconfig',values['KUBECONFIG'] if values else os.environ['KUBECONFIG'],'--context',values['KUBE_CONTEXT'] if values else os.environ['KUBE_CONTEXT'],'--request-timeout=0','-n',ns,'exec','-i',name+'-0','-c','sidecar','--','mysqlsh','--uri','root@127.0.0.1:3306','--passwords-from-stdin','--py','--quiet-start=2','-e',code]
 result=subprocess.run(args,input=path.read_text().rstrip('\n')+'\n',capture_output=True,text=True,timeout=120)
 require(result.returncode==0,'MySQL Shell operation failed: '+result.stderr.strip()[-700:])
 marker='MYSQL_JSON=';require(marker in result.stdout,'No MySQL status marker returned.')
 return json.JSONDecoder().raw_decode(result.stdout.split(marker,1)[1])[0]

def verify():
 require(os.environ['MYSQL_SITE_ROLE']=='PRIMARY','Run ClusterSet verification from the London primary folder.')
 peer=peer_env();peer_get(peer,'innodbcluster',peer['MYSQL_CLUSTER_NAME'],peer['MYSQL_NAMESPACE'])
 code='import json; c=dba.get_cluster(); s=c.get_cluster_set(); print("MYSQL_JSON="+json.dumps(dict(cluster=c.name,status=s.status({"extended":1}),readOnly=shell.get_session().run_sql("SELECT @@GLOBAL.super_read_only").fetch_one()[0])))'
 primary=sql(code);replica=sql(code,peer);status=primary['status']
 require(status.get('status')=='HEALTHY' and status.get('primaryCluster')=='cluster1','ClusterSet not healthy with expected primary.')
 require(status.get('clusters',{}).get('cluster2',{}).get('clusterRole')=='REPLICA' and status['clusters']['cluster2'].get('globalStatus')=='OK','Frankfurt is not a healthy ClusterSet replica.')
 require(replica['readOnly']==1 and primary['readOnly']==0 and replica['status'].get('domainName')==status.get('domainName'),'Replica read-only / domain check failed.')
 state_file('clusterset-status.json',status);print(json.dumps(status,indent=2));print('VERIFIED: London cluster1 PRIMARY; Frankfurt cluster2 REPLICA; HEALTHY and replica read-only.',flush=True)

def replication_test():
 import uuid
 require(os.environ['MYSQL_SITE_ROLE']=='PRIMARY','Run the write/read test from London.')
 peer=peer_env();peer_get(peer,'innodbcluster',peer['MYSQL_CLUSTER_NAME'],peer['MYSQL_NAMESPACE']);token=str(uuid.uuid4())
 code='import json; s=shell.get_session(); s.run_sql("CREATE DATABASE IF NOT EXISTS mysql_operator_validation"); s.run_sql("CREATE TABLE IF NOT EXISTS mysql_operator_validation.replication_probe (id VARCHAR(36) PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"); s.run_sql("INSERT INTO mysql_operator_validation.replication_probe(id) VALUES (?)",['+repr(token)+']); print("MYSQL_JSON="+json.dumps({"inserted":True}))'
 sql(code);deadline=time.monotonic()+300
 while True:
  code='import json; s=shell.get_session(); tables=s.run_sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?", ["mysql_operator_validation","replication_probe"]).fetch_one()[0]; print("MYSQL_JSON="+json.dumps({"found":s.run_sql("SELECT COUNT(*) FROM mysql_operator_validation.replication_probe WHERE id=?",['+repr(token)+']).fetch_one()[0] if tables else 0}))'
  if sql(code,peer)['found']==1:break
  require(time.monotonic()<deadline,'Replication probe did not reach Frankfurt within timeout.');time.sleep(5)
 state_file('replication-test.json',dict(id=token,verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),replica='cluster2'))
 print('PASS: London write observed on Frankfurt replica; test row retained.',flush=True)

def prepare_storage():
 guard();rt.workers(get('nodes'))
 existing=get('storageclass',os.environ['STORAGE_CLASS'],optional=True)
 if existing:
  require(existing['provisioner']=='blockvolume.csi.oraclecloud.com' and existing['reclaimPolicy']=='Retain' and existing.get('volumeBindingMode')=='WaitForFirstConsumer' and existing.get('parameters',{}).get('attachment-type')=='paravirtualized','Existing selected class differs; no adoption.')
  print('Selected OCI storage class exists; verifying CSI rollout, no reinstallation.',flush=True)
  for resource in ['deployment/csi-oci-controller','daemonset/csi-oci-node']:
   oc('--request-timeout=0','-n','oci-csi','rollout','status',resource,'--timeout=600s',timeout=630)
  return
 import oci_storage
 oci_storage.main()

def audit_storage():
 import oci
 import oci_storage
 cid,_,region,compute,_=oci_storage.clients()
 storage=oci.core.BlockstorageClient({'region':region},signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
 inventory=oci_storage.scope();workers={n['instance_id'] for n in inventory['nodes'] if n['node']['metadata']['labels'].get('workload')=='mysql-cluster' and not any(k in n['node']['metadata']['labels'] for k in ('node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane'))}
 ns=os.environ['MYSQL_NAMESPACE'];name=os.environ['MYSQL_CLUSTER_NAME']
 claims=[p for p in get('pvc',namespace=ns)['items'] if p['metadata']['name'].startswith('datadir-'+name+'-')]
 require(len(claims)==int(os.environ['MYSQL_INSTANCES']),'Unexpected database PVC count.')
 result=[]
 for claim in claims:
  require(claim['status'].get('phase')=='Bound','Database PVC is not Bound.')
  pv=get('pv',claim['spec']['volumeName']);require(pv['spec']['claimRef']['uid']==claim['metadata']['uid'],'PVC/PV identity mismatch.')
  require(pv['spec']['csi']['driver']=='blockvolume.csi.oraclecloud.com','Unexpected CSI driver.')
  volume=storage.get_volume(pv['spec']['csi']['volumeHandle']).data
  require(volume.compartment_id==cid and volume.lifecycle_state=='AVAILABLE','Volume scope/state mismatch.')
  attachments=oci.pagination.list_call_get_all_results(compute.list_volume_attachments,compartment_id=cid,volume_id=volume.id).data
  active=[a for a in attachments if a.lifecycle_state=='ATTACHED']
  require(len(active)==1 and active[0].compartment_id==cid and active[0].instance_id in workers,'Volume is not attached exclusively to an authorized worker.')
  result.append(dict(namespace=ns,cluster=name,pvc=claim['metadata']['name'],volume_id=volume.id,size_gib=volume.size_in_gbs,region=region,compartment_id=cid,worker_instance_id=active[0].instance_id))
 state_file('volume-audit.json',result)
 print('VERIFIED: '+str(len(result))+' database volume(s) in selected compartment/region, attached to selected workers.',flush=True)

def main(action):
 guard()
 if action=='label':
  import label_workers;label_workers.main()
 elif action=='storage':prepare_storage()
 elif action=='security':security()
 elif action=='values':print(json.dumps(operator_values(),indent=2))
 elif action=='export':export_service()
 elif action=='create':create_cluster()
 elif action=='wait':wait_cluster()
 elif action=='verify':verify()
 elif action=='replication-test':replication_test()
 elif action=='audit':audit_storage()
 elif action=='validate':rt.capacity(get('nodes'),oc('get','pods','-A','-o','json',json_output=True))
 else:raise ValueError('Unknown operation.')

if __name__=='__main__':
 os.umask(0o077)
 try:main(sys.argv[1])
 except Exception as error:sys.exit('MySQL regional step failed: '+str(error))
