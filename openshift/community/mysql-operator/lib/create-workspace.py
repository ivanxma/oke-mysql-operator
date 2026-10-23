"""Create two regional MySQL execution folders without Mac configuration uploads."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[4]
FIELDS=('KUBECONFIG','ADMIN_WORKDIR','OCI_REGION','OCI_COMPARTMENT_ID','OCI_COMPARTMENT_NAME','CLUSTER_NAME','BASE_DOMAIN','OKD_STATE_DIR')
def require(value,message):
 if not value:raise ValueError(message)
def profile(path,region):
 require(path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o077,'Protected generated OKD profile required.')
 code='import os,json;print(json.dumps({k:os.environ.get(k, "") for k in '+repr(FIELDS)+'}))'
 v=json.loads(subprocess.check_output(['bash','-c','set -e; source "$1" "$2"; python3 -c "$3"','--',str(ROOT/'openshift/community/regions'/region/'env.sh'),str(path),code],text=True,timeout=30))
 require(all(v.values()) and v['OCI_REGION']==region,'Incomplete regional OKD selection.')
 return v

def main():
 p=argparse.ArgumentParser();p.add_argument('--deployment',default='jumphost');p.add_argument('--lab',action='store_true');p.add_argument('--password-stdin',action='store_true');a=p.parse_args()
 require(a.lab,'This first cut uses one worker/member per site; acknowledge --lab.')
 require(a.password_stdin,'Supply the lab password on stdin with --password-stdin; it is never an argument or public setting.')
 require(a.deployment and all(c.isalnum() or c in '-_' for c in a.deployment),'Invalid deployment folder name.')
 password=sys.stdin.read().rstrip('\n');require(len(password)>=8 and not any(c in password for c in '\n\r\0'),'Lab password must be one line, at least 8 characters.')
 sites=[]
 for region,role,name,other,base in [('uk-london-1','PRIMARY','cluster1','cluster2',1000),('eu-frankfurt-1','REPLICA','cluster2','cluster1',2000)]:
  folder=ROOT/'generated/openshift'/region/a.deployment;v=profile(folder/'community.env',region)
  oc=[str(Path(v['ADMIN_WORKDIR'])/'bin/oc'),'--kubeconfig',v['KUBECONFIG'],'--request-timeout=20s']
  def get(*args):return json.loads(subprocess.check_output(oc+list(args)+['-o','json'],text=True,timeout=30))
  expected='https://api.'+v['CLUSTER_NAME']+'.'+v['BASE_DOMAIN']+':6443'
  cfg=get('config','view','--minify');require(cfg['clusters'][0]['cluster']['server']==expected,'Wrong OKD API.')
  uid=get('get','namespace','kube-system')['metadata']['uid'];nodes=get('get','nodes')['items']
  workers=[n for n in nodes if 'node-role.kubernetes.io/worker' in n['metadata']['labels'] and not any(k in n['metadata']['labels'] for k in ['node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane'])]
  require(len(workers)==1 and any(c['type']=='Ready' and c['status']=='True' for c in workers[0]['status']['conditions']),'The lab needs exactly one Ready worker per region.')
  sites.append(dict(folder=folder,values=v,uid=uid,worker=workers[0]['metadata']['name'],role=role,name=name,other=other,base=base))
 require(sites[0]['uid']!=sites[1]['uid'] and sites[0]['values']['OCI_COMPARTMENT_ID']==sites[1]['values']['OCI_COMPARTMENT_ID'],'Select distinct clusters in the same authorized compartment.')
 for s in sites:
  folder=s['folder']/'mysql-operator';require(not folder.is_symlink(),'Refusing symlink execution folder.');folder.mkdir(mode=0o700,exist_ok=True)
  cfg=folder/'config.env'
  if cfg.exists():
   require(not cfg.is_symlink() and not cfg.stat().st_mode & 0o077,'Protect existing configuration.');text=cfg.read_text()
   require('MYSQL_OKD_UID='+shlex.quote(s['uid'])+'\n' in text,'Existing MySQL profile belongs to another OKD cluster.')
  else:
   text=(Path(__file__).parents[1]/'config.env.example').read_text()
   replacements=dict(MYSQL_SITE_ROLE=s['role'],MYSQL_OKD_UID=s['uid'],MYSQL_SUBMARINER_ID=s['values']['CLUSTER_NAME'],MYSQL_PEER_REGION='eu-frankfurt-1' if s['role']=='PRIMARY' else 'uk-london-1',MYSQL_WORKER_NAMES=s['worker'],MYSQL_NAMESPACE='mysql-'+s['name'],MYSQL_CLUSTER_NAME=s['name'],MYSQL_SECRET_NAME=s['name']+'-root',MYSQL_BASE_SERVER_ID=str(s['base']),PRIMARY_MYSQL_NAMESPACE='mysql-'+s['name'],PRIMARY_MYSQL_CLUSTER_NAME=s['name'],REPLICA_MYSQL_NAMESPACE='mysql-'+s['other'],REPLICA_MYSQL_CLUSTER_NAME=s['other'],REPLICA_MYSQL_SECRET_NAME=s['other']+'-root')
   lines=[]
   for line in text.splitlines():
    key=line.split('=',1)[0]
    lines.append(key+'='+shlex.quote(replacements[key]) if key in replacements else line)
   cfg.write_text('\n'.join(lines)+'\n');cfg.chmod(0o600)
  creds=folder/'.credentials';creds.mkdir(mode=0o700,exist_ok=True);pw=creds/'root-password'
  require(not pw.is_symlink(),'Refusing symlink credential file.')
  if pw.exists():require(pw.read_text().rstrip('\n')==password and not pw.stat().st_mode & 0o077,'Existing password differs or has unsafe mode; never rotate implicitly.')
  else:
   fd=os.open(pw,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as f:f.write(password+'\n')
  (folder/'.state').mkdir(mode=0o700,exist_ok=True)
  env='''#!/usr/bin/env bash
# Generated regional MySQL environment.
_mysql_profile=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$_mysql_profile/../00-env.sh"
export MYSQL_REGIONAL_CONFIG="$_mysql_profile/config.env"
_mysql_repo=$(cd -- "$_mysql_profile/../../../../.." && pwd)
source "$_mysql_repo/openshift/community/mysql-operator/env.sh"
unset _mysql_profile _mysql_repo
'''
  (folder/'00-env.sh').write_text(env);(folder/'00-env.sh').chmod(0o700)
  for source in Path(__file__).parents[1].glob('[0-9][0-9]-*.sh'):
   if source.name=='00-create-workspace.sh':continue
   dest=folder/source.name
   require(not dest.is_symlink() and (not dest.exists() or '# Generated regional MySQL launcher.' in dest.read_text()),'Unrecognized generated file; preserve it.')
   dest.write_text('''#!/usr/bin/env bash
# Generated regional MySQL launcher.
set -euo pipefail
folder=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$folder/00-env.sh"
repo=$(cd -- "$folder/../../../../.." && pwd)
exec bash "$repo/openshift/community/mysql-operator/'''+source.name+'''" "$@"
''');dest.chmod(0o700)
  print(s['values']['OCI_REGION']+': private MySQL execution folder '+str(folder)+'; '+s['name']+' '+s['role']+'.')
 print('Only local private files created. No namespaces, nodes, storage or databases changed; lab credentials were not printed.')

if __name__=='__main__':
 os.umask(0o077)
 try:main()
 except Exception as error:sys.exit('Workspace creation failed: '+str(error))
