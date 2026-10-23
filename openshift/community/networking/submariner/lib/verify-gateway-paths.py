"""Repeat the two-way gateway probes using selected profiles and SSH metadata."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from workflow import Workflow, require, private_write, run

PROBE=r'''
import json,socket,ssl,subprocess,sys,urllib.request,urllib.error
v=json.load(sys.stdin)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def metadata(path):
 req=urllib.request.Request('http://169.254.169.254/opc/v2/'+path,headers={'Authorization':'Bearer Oracle'})
 with opener.open(req,timeout=5) as r:return json.load(r)
i=metadata('instance/');vnics=metadata('vnics/')
assert i['id']==v['instance_id'] and i['compartmentId']==v['compartment_id']
assert any(n.get('privateIp')==v['address'] for n in vnics)
assert socket.gethostname().lower().rstrip('.')==v['gateway']
route=subprocess.run(['ip','route','get',v['peer_address']],capture_output=True,text=True,timeout=5)
ping=subprocess.run(['ping','-c','3','-W','3','-M','do','-s','1400',v['peer_address']],capture_output=True,text=True,timeout=15)
ctx=ssl.create_default_context(cadata=v['broker_ca'])
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ctx))
try:
 with client.open(v['broker_api']+'/readyz',timeout=10) as response:code=response.status
except urllib.error.HTTPError as error:code=error.code
print(json.dumps(dict(route_ok=route.returncode==0,ping_ok=ping.returncode==0,broker_http=code,
 broker_tls_verified=True,gateway=v['gateway'],address=v['address'],peer_address=v['peer_address'])))
'''


def check_result(value):
    require(value.get('route_ok') and value.get('ping_ok'),'Gateway route/DF ping probe failed; no configuration changes made.')
    require(value.get('broker_tls_verified') and value.get('broker_http') in (200,401,403),
            'Broker TLS/path probe failed; no certificate bypass or credential changes.')


def main():
    w=Workflow();w.peer_allowed();info=w.validation()
    # Verify live VM/image/compartment bindings and existing DRG paths first.
    import importlib.util
    module_path=Path(__file__).with_name('gateway-network.py')
    s=importlib.util.spec_from_file_location('gateway_scope',module_path);network=importlib.util.module_from_spec(s);s.loader.exec_module(network)
    network.main('preview')
    root=Path(__file__).resolve().parents[5]
    s=importlib.util.spec_from_file_location('profiles',root/'openshift/community/lib/sync-regional-dns.py');profiles=importlib.util.module_from_spec(s);s.loader.exec_module(profiles)
    selected=Path(w.env['OKD_CONFIG_FILE']);peer=json.loads((w.state/'peer-registration.json').read_text())
    sites=[]
    for role,region in [('target','uk-london-1'),('peer',peer['region'])]:
        v=profiles.profile(root/'generated/openshift'/region/selected.parent.name/'community.env',region)
        node=w.read(role=='peer','get','node',info[role]['gateway'])
        address=next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')
        records=json.loads((Path(v['OKD_STATE_DIR'])/'worker-nodes.json').read_text())
        require(records['cluster_uid']==info[role]['uid'],'Worker registration cluster identity differs.')
        matches=[iid for iid,item in records['workers'].items() if item['name']==info[role]['gateway'] and item['address']==address and item['node_uid']==node['metadata']['uid']]
        require(len(matches)==1,'Gateway must match an enrolled worker identity.')
        sites.append(dict(role=role,values=v,address=address,instance_id=matches[0],gateway=info[role]['gateway']))
    base,_,_=w.selection()
    ca=base64.b64decode(run(base+['config','view','--raw','--minify','-o','jsonpath={.clusters[0].cluster.certificate-authority-data}']).strip(),validate=True).decode()
    require('-----BEGIN CERTIFICATE-----' in ca,'Configured API CA is required for the gateway TLS probe.')
    results=[]
    for n,site in enumerate(sites):
        config=Path(site['values']['OKD_STATE_DIR'])/'ssh_config';require(config.is_file(),'Refresh this region SSH state with step 01 --ssh-only.')
        payload=dict(instance_id=site['instance_id'],compartment_id=site['values']['OCI_COMPARTMENT_ID'],
                     address=site['address'],gateway=site['gateway'],peer_address=sites[1-n]['address'],
                     broker_ca=ca,broker_api=w.env['SUBMARINER_TARGET_API'])
        print('CHECK '+site['values']['OCI_REGION']+': verified worker SSH identity, private route, DF ping (1400-byte payload), broker TLS...',flush=True)
        # Only CA and identity expectations cross stdin; administrative credentials stay on admin.
        import shlex
        result=subprocess.run(['ssh','-F',str(config),'-o','BatchMode=yes','-o','ConnectTimeout=10','-o','HostName='+site['address'],
                               'okd-target',shlex.join(['python3','-c',PROBE])],input=json.dumps(payload),capture_output=True,text=True,timeout=60)
        require(result.returncode==0,'Gateway SSH/probe failed. Review SSH identity, route and private node state; no host key was removed.')
        value=json.loads(result.stdout);check_result(value);results.append(dict(region=site['values']['OCI_REGION'],cluster_uid=info[site['role']]['uid'],**value))
        print('PASS: private route/DF ping; certificate-verified broker HTTP '+str(value['broker_http'])+'. HTTP 401/403 means path reached, not administrative authorization.',flush=True)
    private_write(w.state/'gateway-paths.json',dict(results=results,ping_payload_bytes=1400))
    print('PATH CHECK PASSED. Read-only probes; UDP/ESP tunnel and workload acceptance still require steps 06 and 08.')


if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except Exception as error:sys.exit('Gateway path verification failed: '+str(error))
