"""Bounded gateway readiness and current-run verification/cleanup evidence."""
import datetime
import json
import os
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET
from workflow import Workflow, require, private_write, local_deployment_ready


def selected(w):
    saved=json.loads((w.state/'clusters.json').read_text())
    roles=['target','peer'] if w.enabled('SUBMARINER_ENABLE_PEER') else ['target']
    for role in roles:
        if role=='peer':w.peer_allowed()
        actual=w.read(role=='peer','get','namespace','kube-system')['metadata']['uid']
        prefix='SUBMARINER_PEER' if role=='peer' else 'SUBMARINER_TARGET'
        require(role in saved and saved[role]['uid']==actual and saved[role]['id']==w.env[prefix+'_ID'] and saved[role]['gateway']==w.env[prefix+'_GATEWAY'],
                'Selected cluster identity differs from the saved networking deployment.')
    return {r:{k:saved[r][k] for k in ('uid','id','gateway')} for r in roles}


def rollout_ready(deployments,daemonsets):
    if not deployments.get('items') or not daemonsets.get('items'):return False
    for d in deployments['items']:
        n=d['spec'].get('replicas',1);s=d.get('status',{})
        if n<1 or s.get('readyReplicas',0)<n or s.get('updatedReplicas',0)<n:return False
    for d in daemonsets['items']:
        s=d.get('status',{});n=s.get('desiredNumberScheduled',0)
        if n<1 or s.get('numberReady')!=n or s.get('updatedNumberScheduled')!=n:return False
    return True


def private_connection(gateway,remote,peer_id):
    status=gateway.get('status',{});other=remote.get('status',{}).get('localEndpoint',{})
    address=other.get('private_ip');links=status.get('connections',[])
    return bool(address and status.get('haStatus')=='active' and not status.get('statusFailure') and
                len(links)==1 and links[0].get('status')=='connected' and
                links[0].get('endpoint',{}).get('cluster_id')==peer_id and links[0].get('usingIP')==address)


def snapshot(w,info):
    docs={};gateways={};ready=True
    for role,item in info.items():
        peer=role=='peer'
        values=[w.read(peer,'get',resource,'-n','submariner-operator') for resource in
                ('deployments','daemonsets','gateways.submariner.io','endpoints.submariner.io')]
        docs[role]=values
        candidates=[g for g in values[2].get('items',[]) if g['metadata']['name']==item['gateway']]
        gateways[role]=candidates[0] if len(candidates)==1 else {}
        status=gateways[role].get('status',{})
        healthy=rollout_ready(*values[:2]);ready=ready and healthy
        print(item['id']+': workloads '+('Ready' if healthy else 'WAITING')+'; gateway '+status.get('haStatus','not published')+
              '; connections '+', '.join(c.get('status','unknown') for c in status.get('connections',[])),flush=True)
    if 'peer' not in info:
        ready=local_deployment_ready(*docs['target'],info['target']['id'],info['target']['gateway'])
    else:
        for role,other in [('target','peer'),('peer','target')]:
            ready=private_connection(gateways[role],gateways[other],info[other]['id']) and ready
    return ready


def wait(w,timeout,interval):
    require(1<=timeout<=7200 and 1<=interval<=300,'Use timeout 1..7200 and interval 1..300 seconds.')
    info=selected(w);start=time.monotonic()
    print('READ-ONLY readiness wait. Ctrl-C stops this monitor only. No joins, restarts or configuration changes.',flush=True)
    while True:
        # Revalidate identity on every iteration; retain the original expected UIDs.
        require(selected(w)==info,'Networking identity changed while waiting.')
        print('Elapsed '+str(int(time.monotonic()-start))+'s; checking gateway/component rollout...',flush=True)
        if snapshot(w,info):
            private_write(w.state/'mesh-readiness.json',dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),clusters=info,ready=True))
            print('READY: '+('both gateways active and connected over expected private peer IPs.' if 'peer' in info else 'local deployment ready; zero remote connections expected.'),flush=True)
            print('Next: step 08 verifies workloads; readiness alone is not a passing workload report.',flush=True);return
        remaining=timeout-(time.monotonic()-start)
        require(remaining>0,'NOT READY: timeout; bootstrap/rollout continues. Inspect step 06 status or step 09 diagnostics; do not rejoin.')
        print('WAITING: retry in '+str(min(interval,int(remaining)))+'s.',flush=True)
        time.sleep(min(interval,remaining))


def junit_summary(path,started):
    require(path.is_file() and not path.is_symlink(),'No current verification report. Run 08-verify-connectivity.sh.')
    require(path.stat().st_mtime>=started.timestamp(),'Stale verification report predates this run.')
    root=ET.parse(path).getroot();cases=root.findall('.//testcase')
    require(bool(cases),'Verification report contains no test cases.')
    failed=sum(c.find('failure') is not None or c.find('error') is not None for c in cases)
    skipped=sum(c.find('skipped') is not None for c in cases)
    # Count aggregate errors as well; a failed suite hook must not be hidden by test cases.
    errors=max([int(n.get('errors',0))+int(n.get('failures',0)) for n in [root,*root.findall('.//testsuite')]])
    result=dict(passed=len(cases)-failed-skipped,failed=max(failed,errors),skipped=skipped)
    require(result['passed']>0 and result['failed']==0,'Verification has failures/errors or no passing tests; inspect private verify.xml.')
    return result


def report(w,timeout=120,interval=5):
    require(0<=timeout<=600 and 1<=interval<=60,'Use cleanup wait 0..600 and interval 1..60 seconds.')
    info=selected(w);require('peer' in info,'Two enrolled clusters required for the mesh verification report.')
    record=json.loads((w.state/'verifier-run.json').read_text())
    require(record.get('clusters')=={r:dict(uid=i['uid'],id=i['id']) for r,i in info.items()},
            'Verification receipt is not bound to these cluster identities. Rerun step 08; legacy evidence is retained.')
    started=datetime.datetime.fromisoformat(record['started_at']);require(started.tzinfo is not None,'Verification start time must include timezone.')
    result=junit_summary(w.state/'verify.xml',started)
    deadline=time.monotonic()+timeout
    while True:
        require(selected(w)==info,'Cluster identity changed during cleanup verification.')
        clean=True
        for role,item in info.items():
            keys={k.split('/',1)[1]:v for k,v in record['namespaces'].items() if k.startswith(item['id']+'/')}
            require(bool(keys),'Missing recorded test namespaces for a participating cluster.')
            namespaces=w.read(role=='peer','get','namespaces')['items']
            remaining=[n['metadata']['name'] for n in namespaces if n['metadata']['name'] in keys]
            bindings=w.read(role=='peer','get','rolebindings','--all-namespaces')['items']
            grants=[b['metadata']['name'] for b in bindings if b['metadata'].get('labels',{}).get('okd-scripts-verifier-run')==record['run_id']]
            print(item['id']+': test namespaces remaining '+str(len(remaining))+'; temporary grants remaining '+str(len(grants)),flush=True)
            clean=clean and not remaining and not grants
        if clean:break
        remaining_time=deadline-time.monotonic()
        require(remaining_time>0,'Test cleanup incomplete after wait. Inspect verifier-run.json; no resources were deleted by this check.')
        print('WAITING: asynchronous test cleanup; retry in '+str(min(interval,int(remaining_time)))+'s. No deletions or permission changes.',flush=True)
        time.sleep(min(interval,remaining_time))
    private_write(w.state/'verification-check.json',dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),run_id=record['run_id'],clusters=record['clusters'],**result,cleanup_verified=True))
    print('VERIFIED: '+str(result['passed'])+' PASS, 0 FAIL, '+str(result['skipped'])+' SKIPPED; run-owned test namespaces and grants removed.',flush=True)
    print('This checks the recorded run and current cleanup; use step 06 for current gateway readiness.',flush=True)


if __name__=='__main__':
    os.umask(0o077)
    try:
        w=Workflow()
        if sys.argv[1]=='wait':wait(w,int(sys.argv[2]),int(sys.argv[3]))
        elif sys.argv[1]=='report':report(w,int(sys.argv[2]) if len(sys.argv)>2 else 120,int(sys.argv[3]) if len(sys.argv)>3 else 5)
        else:raise ValueError('Unknown operation')
    except Exception as error:sys.exit('CHECK FAILED: '+str(error))
