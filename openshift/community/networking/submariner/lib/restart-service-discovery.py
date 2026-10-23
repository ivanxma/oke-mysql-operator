"""Reload Lighthouse clients using existing, TLS-verified broker credentials."""
import base64
import json
import ssl
import subprocess
import sys
import time
import urllib.request
from workflow import Workflow, require, run
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('mesh',Path(__file__).with_name('mesh-readiness.py'))
mesh=importlib.util.module_from_spec(spec);spec.loader.exec_module(mesh)

def plan(w):
    info=mesh.selected(w);result=[]
    expected=w.env['SUBMARINER_TARGET_API'].rstrip('/')
    for role in info:
        peer=role=='peer';base,_,_=w.selection(peer)
        crs=w.read(peer,'get','submariners.submariner.io','-n','submariner-operator')['items']
        require(len(crs)==1,'Expected exactly one existing Submariner deployment.')
        cr=crs[0]['spec'];api='https://'+cr['brokerK8sApiServer'].removeprefix('https://')
        require(api.rstrip('/')==expected,'Broker API differs from the selected London API.')
        require(cr['brokerK8sSecret']=='submariner-broker-secret','Unexpected broker credential source.')
        secret=w.read(peer,'get','secret',cr['brokerK8sSecret'],'-n','submariner-operator')
        ca=base64.b64decode(secret['data']['ca.crt'],validate=True).decode()
        token=base64.b64decode(secret['data']['token'],validate=True).decode()
        request=urllib.request.Request(api+'/apis/multicluster.x-k8s.io/v1alpha1/namespaces/'+cr['brokerK8sRemoteNamespace']+'/serviceimports?limit=1',headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(request,context=ssl.create_default_context(cadata=ca),timeout=20) as response:
            require(response.status==200,'Broker credentials cannot read ServiceImports.')
        d=w.read(peer,'get','deployment','submariner-lighthouse-agent','-n','submariner-operator')
        require(d['metadata']['namespace']=='submariner-operator' and d['spec']['replicas']==1,'Unexpected Lighthouse deployment.')
        print(info[role]['id']+': saved identity matches; current broker CA, hostname and token verified. Plan: restart Lighthouse agent only.',flush=True)
        replicas=w.read(peer,'get','replicasets','-n','submariner-operator')['items']
        owned={r['metadata']['uid'] for r in replicas if any(o.get('uid')==d['metadata']['uid'] and o.get('controller') for o in r['metadata'].get('ownerReferences',[]))}
        pods=w.read(peer,'get','pods','-n','submariner-operator')['items']
        pods=[p for p in pods if not p['metadata'].get('deletionTimestamp') and any(o.get('uid') in owned and o.get('controller') for o in p['metadata'].get('ownerReferences',[]))]
        require(len(pods)==1,'Expected exactly one agent pod owned by the selected Deployment.')
        result.append((base,d,pods[0]))
    return result

def main():
    w=Workflow();plans=plan(w)
    if '--apply' not in sys.argv:
        print('PREVIEW ONLY. Use --apply to reload existing Lighthouse clients. No CA/token, tunnel, CNI or DNS record changes.');return
    for base,d,pod in plans:
        # The Submariner operator reconciles Deployment annotations. Recycle only
        # the UID-bound agent Pod so it reloads the projected broker CA.
        options={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':pod['metadata']['uid']},'gracePeriodSeconds':30}
        run(base+['delete','--raw','/api/v1/namespaces/submariner-operator/pods/'+pod['metadata']['name'],'-f','-'],data=json.dumps(options))
        deadline=time.monotonic()+180
        while True:
            pods=json.loads(subprocess.check_output(base+['-n','submariner-operator','get','pods','-l','app=submariner-lighthouse-agent','-o','json'],text=True,timeout=30))['items']
            fresh=[p for p in pods if p['metadata']['uid']!=pod['metadata']['uid'] and not p['metadata'].get('deletionTimestamp') and any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[]))]
            if fresh:break
            require(time.monotonic()<deadline,'New Lighthouse agent not Ready before timeout.')
            print('Waiting for a new Ready Lighthouse agent pod...',flush=True);time.sleep(5)
    print('Lighthouse clients restarted. Recheck ServiceExport Synced status and remote pod DNS; tunnel readiness alone does not prove discovery.')

if __name__=='__main__':
    try:main()
    except Exception as e:sys.exit('Service discovery reload failed: '+type(e).__name__+'; inspect private diagnostics. No trust bypass applied.')
