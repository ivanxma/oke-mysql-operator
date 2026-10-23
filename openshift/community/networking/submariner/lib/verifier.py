"""Run native verification with SCC access confined to its fresh test namespaces."""
import datetime
import json
import re
import secrets
import subprocess
import time
from workflow import require, private_write, run

NAMESPACE=re.compile(r'^e2e-tests-[a-z0-9-]+$')
MESSAGE=re.compile(r'(?:Generated|Creating) namespace "([^"]+)" in cluster "([^"]+)"')
ROLE='system:openshift:scc:nonroot-v2'
NAME='submariner-verifier-nonroot'


def checked_namespace(namespace,name,started):
    require(bool(NAMESPACE.fullmatch(name)) and len(name)<=63,'Unexpected verifier namespace name.')
    require(namespace['metadata']['name']==name,'Verifier namespace name mismatch.')
    created=datetime.datetime.fromisoformat(namespace['metadata']['creationTimestamp'].replace('Z','+00:00'))
    require(created>=started-datetime.timedelta(seconds=5),'Refusing existing namespace from before this verifier run.')
    labels=namespace['metadata'].get('labels',{})
    require(labels.get('pod-security.kubernetes.io/enforce')=='privileged' and
            labels.get('security.openshift.io/scc.podSecurityLabelSync')=='false',
            'Namespace does not have the upstream verifier labels.')
    return namespace['metadata']['uid']


def binding(name,uid,run_id):
    return dict(apiVersion='rbac.authorization.k8s.io/v1',kind='RoleBinding',
                metadata=dict(name=NAME,namespace=name,labels={'okd-scripts-verifier-run':run_id},
                              ownerReferences=[dict(apiVersion='v1',kind='Namespace',name=name,uid=uid)]),
                roleRef=dict(apiGroup='rbac.authorization.k8s.io',kind='ClusterRole',name=ROLE),
                subjects=[dict(kind='ServiceAccount',name='default',namespace=name)])


def verify(w):
    started=datetime.datetime.now(datetime.timezone.utc);run_id=secrets.token_hex(16)
    sides={}
    for peer in (False,True):
        prefix='SUBMARINER_PEER' if peer else 'SUBMARINER_TARGET'
        base,path,context=w.selection(peer)
        role=w.read(peer,'get','clusterrole',ROLE)
        require(role['metadata']['name']==ROLE and role.get('rules')==[dict(apiGroups=['security.openshift.io'],resources=['securitycontextconstraints'],resourceNames=['nonroot-v2'],verbs=['use'])],
                'Built-in nonroot-v2 role differs; stop before test permissions.')
        sides[w.env[prefix+'_ID']]=dict(peer=peer,base=base,path=path,context=context)
    target=sides[w.env['SUBMARINER_TARGET_ID']];peer=sides[w.env['SUBMARINER_PEER_ID']]
    require(w.version in run([str(w.bin),'version']),'Wrong subctl version.')
    args=[str(w.bin),'verify','--only','connectivity,service-discovery','--disruptive-tests=false','--verbose',
          '--junit-report',str(w.state/'verify.xml'),'--kubeconfig',str(target['path']),'--context',target['context'],
          '--toconfig',str(peer['path']),'--tocontext',peer['context']]
    recorded={};granted=[];process=None
    def persist():private_write(w.state/'verifier-run.json',dict(run_id=run_id,started_at=started.isoformat(),namespaces=recorded))
    persist()
    try:
        process=subprocess.Popen(args,cwd=w.state,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in process.stdout:
            print(line,end='',flush=True)
            found=MESSAGE.search(line)
            if not found:continue
            name,cluster_id=found.groups();require(cluster_id in sides,'Unexpected verifier cluster ID.')
            key=cluster_id+'/'+name
            if key in recorded:continue
            side=sides[cluster_id];base=side['base']
            deadline=time.monotonic()+3
            while True:
                result=subprocess.run(base+['get','namespace',name,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=40)
                require(result.returncode==0,'Cannot inspect verifier namespace; no permissions granted.')
                if result.stdout.strip():break
                if time.monotonic()>=deadline:break
                time.sleep(.2)
            if not result.stdout.strip():
                print('VERIFIER: test namespace already absent; no permission change.',flush=True);continue
            namespace=json.loads(result.stdout);uid=checked_namespace(namespace,name,started)
            if namespace.get('metadata',{}).get('deletionTimestamp') or namespace.get('status',{}).get('phase')=='Terminating':
                print('VERIFIER: test namespace already terminating; no permission change.',flush=True);continue
            existing=run(base+['get','rolebinding',NAME,'-n',name,'--ignore-not-found','-o','json']).strip()
            require(not existing,'Unexpected existing verifier RoleBinding; preserve it and stop.')
            # Recheck namespace identity immediately before this namespace-only grant.
            latest=subprocess.run(base+['get','namespace',name,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=40)
            require(latest.returncode==0,'Cannot recheck namespace identity.')
            if not latest.stdout.strip():continue
            latest=json.loads(latest.stdout);require(latest['metadata']['uid']==uid,'Verifier namespace identity changed.')
            if latest['metadata'].get('deletionTimestamp'):continue
            result=subprocess.run(base+['create','-f','-','-o','json'],input=json.dumps(binding(name,uid,run_id)),capture_output=True,text=True,timeout=40)
            if result.returncode:
                after=subprocess.run(base+['get','namespace',name,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=40)
                require(after.returncode==0,'Cannot inspect test namespace after grant failure.')
                if not after.stdout.strip():continue
                after=json.loads(after.stdout);require(after['metadata']['uid']==uid,'Verifier namespace identity changed after grant failure.')
                if after['metadata'].get('deletionTimestamp') or after.get('status',{}).get('phase')=='Terminating':
                    print('VERIFIER: test completed during namespace grant; no permission change.',flush=True);continue
                raise ValueError('Test namespace admission grant failed: '+result.stderr.strip()[:300])
            created=json.loads(result.stdout);granted.append((side,name,created['metadata']['uid']))
            recorded[key]=dict(namespace_uid=uid,rolebinding_uid=created['metadata']['uid']);persist()
            print('VERIFIER: temporary nonroot-v2 access for default SA in '+name+' on '+cluster_id+' only.',flush=True)
        code=process.wait()
        require(code==0,'Native Submariner verification failed (exit '+str(code)+'); inspect private verify.xml.')
    finally:
        if process and process.poll() is None:
            process.send_signal(2)
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        for side,name,uid in granted:
            result=subprocess.run(side['base']+['get','rolebinding',NAME,'-n',name,'--ignore-not-found','-o','json'],capture_output=True,text=True,timeout=40)
            require(result.returncode==0,'Cannot verify temporary test RBAC cleanup; inspect private verifier-run.json.')
            if not result.stdout.strip():continue
            current=json.loads(result.stdout)
            require(current['metadata']['uid']==uid and current['metadata'].get('labels',{}).get('okd-scripts-verifier-run')==run_id,
                    'Temporary test binding identity changed; no deletion.')
            run(side['base']+['delete','--raw=/apis/rbac.authorization.k8s.io/v1/namespaces/'+name+'/rolebindings/'+NAME,'-f','-'],
                json.dumps(dict(apiVersion='v1',kind='DeleteOptions',preconditions=dict(uid=uid))))
