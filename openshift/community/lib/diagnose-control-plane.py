"""Read-only bootstrap evidence and guarded controller-manager installer recovery."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def conditions(resource):
    return {c['type']:c.get('status','Unknown') for c in resource.get('status',{}).get('conditions',[])}


def recovery_plan(operator, pods, node_name):
    status = operator.get('status',{})
    revision = status.get('latestAvailableRevision',0)
    states = status.get('nodeStatuses',[])
    if operator.get('spec',{}).get('managementState') != 'Managed':
        raise ValueError('Controller-manager must be Managed')
    if len(states) != 1 or states[0].get('nodeName') != node_name:
        raise ValueError('Recovery supports only the selected single-node control plane')
    state = states[0]
    if revision <= state.get('currentRevision',0):
        return None
    if str(operator.get('spec',{}).get('forceRedeploymentReason','')).startswith('okd-bootstrap-recovery-'):
        raise ValueError('A scripted recovery was already requested; inspect its progress before another rollout')
    installers = [p for p in pods if p.get('metadata',{}).get('name','').startswith('installer-')
                  and p.get('spec',{}).get('nodeName') == node_name]
    current = [p for p in installers if re.match(r'installer-'+str(revision)+r'(?:-|$)',p['metadata']['name'])]
    if not current or any(p.get('status',{}).get('phase') != 'Failed' for p in current):
        raise ValueError('Latest installer is not confirmed failed; allow active/completed revisions to reconcile')
    evidence = ' '.join(str(p.get('status',{})) for p in current).lower()
    if not any(term in evidence for term in ('timeout','timed out','deadline exceeded')):
        raise ValueError('Installer failure is not a confirmed API timeout; investigate its actual cause')
    return dict(revision=revision,current_revision=state.get('currentRevision',0),
                failed_installers=[p['metadata']['name'] for p in current])


def run(work, selected, domain, address, mode):
    if mode not in ('diagnose','preview','apply'):
        raise ValueError('Invalid diagnostic/recovery mode')
    root, kubeconfig = Path(work), Path(selected)
    if not kubeconfig.is_absolute() or not kubeconfig.is_file() or not kubeconfig.samefile(root/'sno/auth/kubeconfig'):
        raise ValueError('KUBECONFIG must select this installation auth file')
    if (root/'cluster-domain').read_text().strip() != domain:
        raise ValueError('Selected cluster/workspace domain mismatch')
    env = dict(os.environ,KUBECONFIG=str(kubeconfig))
    oc = str(root/'bin/oc')
    def command(args):
        result = subprocess.run([oc,'--request-timeout=20s',*args],env=env,
                                capture_output=True,text=True,timeout=35)
        if result.returncode:
            raise ValueError((result.stderr.strip() or 'API command failed')[:500])
        return result.stdout
    server = command(['config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}']).strip()
    if server != 'https://api.'+domain+':6443':
        raise ValueError('Configured API differs from selected cluster')
    print('Read-only evidence collection started. Each API request allows 20s; command limit 35s.',flush=True)
    report = dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),cluster=domain,mode=mode)
    def read(label,path,raw=False):
        print('Checking '+label+'...',flush=True)
        output = command(['get','--raw='+path])
        return output.strip() if raw else json.loads(output)
    report['readyz'] = read('API readiness','/readyz?verbose',True)
    nodes = read('authenticated node API','/api/v1/nodes')['items']
    if len(nodes) != 1 or not any(a.get('type')=='InternalIP' and a.get('address')==address
                                for a in nodes[0].get('status',{}).get('addresses',[])):
        raise ValueError('Expected one node with registered private address; refusing another cluster/topology')
    node = nodes[0]
    report['node'] = dict(name=node['metadata']['name'],conditions=node.get('status',{}).get('conditions',[]))
    operators = read('cluster operators','/apis/config.openshift.io/v1/clusteroperators')['items']
    report['operators'] = [dict(name=o['metadata']['name'],conditions=o.get('status',{}).get('conditions',[])) for o in operators]
    for o in report['operators']:
        c = {v['type']:v['status'] for v in o['conditions']}
        print('%s: Available=%s Progressing=%s Degraded=%s' % (o['name'],c.get('Available','Unknown'),c.get('Progressing','Unknown'),c.get('Degraded','Unknown')),flush=True)
        if c.get('Available') != 'True' or c.get('Degraded') == 'True':
            for v in o['conditions']:
                if v.get('message'):
                    print('  '+v['type']+': '+v['message'].splitlines()[0][:350],flush=True)
    apis = read('aggregated API registration','/apis/apiregistration.k8s.io/v1/apiservices')['items']
    report['unavailable_apis'] = [dict(name=a['metadata']['name'],ca_present=bool(a.get('spec',{}).get('caBundle')),
         conditions=a.get('status',{}).get('conditions',[])) for a in apis
         if a.get('spec',{}).get('service') and conditions(a).get('Available') != 'True']
    for a in report['unavailable_apis']:
        print('WAITING aggregated API '+a['name']+': CA bundle '+('present' if a['ca_present'] else 'MISSING'),flush=True)
    operator = read('controller-manager revision','/apis/operator.openshift.io/v1/kubecontrollermanagers/cluster')
    pods = read('controller-manager installers','/api/v1/namespaces/openshift-kube-controller-manager/pods')['items']
    report['controller_manager'] = dict(metadata=operator['metadata'],spec=operator.get('spec',{}),status=operator.get('status',{}))
    report['installers'] = [p for p in pods if p['metadata']['name'].startswith('installer-')]
    directory = root/'diagnostics'
    if directory.is_symlink():
        raise ValueError('Refusing symlink diagnostic directory')
    directory.mkdir(mode=0o700,exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError('Diagnostic directory must restrict group/other access')
    fd,path = tempfile.mkstemp(prefix='control-plane-',suffix='.json',dir=directory)
    with os.fdopen(fd,'w') as output:
        json.dump(report,output,indent=2)
    print('Private evidence report: '+path,flush=True)
    if mode == 'diagnose':
        print('RESULT: '+('RESOURCE CHECKS PASSED; installer completion still requires step 05 wait.'
              if all(conditions(o).get('Available')=='True' and conditions(o).get('Progressing')=='False' and conditions(o).get('Degraded')=='False' for o in operators)
              else 'BOOTSTRAP INCOMPLETE; use operator/revision evidence above. No repair performed.'),flush=True)
        return 0
    if report['readyz'].splitlines()[-1:] != ['readyz check passed'] or conditions(node).get('Ready') != 'True':
        raise ValueError('Healthy API and Ready node required before recovery')
    critical = {o['metadata']['name']:conditions(o) for o in operators}
    if any(critical.get(name,{}).get('Available')!='True' or critical.get(name,{}).get('Degraded')!='False'
           for name in ('kube-apiserver','etcd')):
        raise ValueError('Kubernetes API and etcd must be Available and not Degraded before recovery')
    plan = recovery_plan(operator,pods,node['metadata']['name'])
    if plan is None:
        print('NO CHANGE: controller-manager has reached its latest revision.',flush=True)
        return 0
    print('PLAN: request a new controller-manager revision after timeout of installer revision '+str(plan['revision']),flush=True)
    if mode == 'preview':
        print('PREVIEW ONLY: rerun 05-recover-controller-manager.sh --apply to request this targeted rollout.',flush=True)
        return 0
    patch = [dict(op='test',path='/metadata/resourceVersion',value=operator['metadata']['resourceVersion']),
             dict(op='add',path='/spec/forceRedeploymentReason',value='okd-bootstrap-recovery-'+report['time'])]
    command(['patch','kubecontrollermanagers.operator.openshift.io','cluster','--type=json','-p',json.dumps(patch)])
    print('APPLIED: requested controller-manager rollout only. Cluster completion is NOT confirmed.',flush=True)
    print('NEXT: ./05-wait-for-control-plane.sh; then ./06-check-control-plane.sh',flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(run(*sys.argv[1:]))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('FAIL: '+str(error),file=sys.stderr)
        sys.exit(1)
