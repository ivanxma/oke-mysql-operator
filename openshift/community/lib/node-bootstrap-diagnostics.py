"""Read node bootstrap evidence over SSH without a functioning Kubernetes API."""
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.request


def redact(text):
    return '\n'.join(line[:500] for line in text.splitlines()
                     if not re.search(r'password|token|secret|authorization:|PRIVATE KEY',line,re.I))


def command(args,sanitize=True):
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=10)
        output=result.stdout+'\n'+result.stderr if sanitize or result.returncode else result.stdout
        return dict(exit=result.returncode,output=redact(output) if sanitize else output)
    except (OSError,subprocess.TimeoutExpired):
        return dict(exit=1,output='Command unavailable or exceeded 10 seconds')


def findings(listeners):
    api=bool(re.search(r':6443\b',listeners))
    etcd=bool(re.search(r':2379\b',listeners))
    if api and not etcd:
        return 'API listener present; etcd listener absent. Resource requests can fail while bootstrap replaces its storage backend. Inspect etcd state before any repair.'
    if api and etcd:
        return 'API and etcd listeners present. This alone does not prove authenticated API access or cluster readiness.'
    return 'API listener not observed. Inspect bootstrap services and logs; installation completion is not confirmed.'


def probe(address,compartment):
    ipaddress.IPv4Address(address)
    request=urllib.request.Request('http://169.254.169.254/opc/v2/vnics/',headers={'Authorization':'Bearer Oracle'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=5) as response:
        vnics=json.load(response)
    if not any(v.get('privateIp')==address for v in vnics):
        raise ValueError('Target metadata differs from registered NODE_IP; no privileged diagnostics run')
    request=urllib.request.Request('http://169.254.169.254/opc/v2/instance/',headers={'Authorization':'Bearer Oracle'})
    with opener.open(request,timeout=5) as response:
        instance=json.load(response)
    if instance.get('compartmentId')!=compartment:
        raise ValueError('Target metadata differs from selected compartment; no privileged diagnostics run')
    result=dict(address=address,compartment=compartment,services=command(['systemctl','show','bootkube.service','kubelet.service','crio.service','--property=Id,ActiveState,SubState']),
                listeners=command(['sudo','-n','ss','-ltnp']),
                bootkube=command(['sudo','-n','journalctl','-u','bootkube.service','--no-pager','-n','35']),
                uptime=command(['uptime']),
                kubelet=command(['sudo','-n','journalctl','-u','kubelet.service','--no-pager','-n','200']),
                static_pods=command(['sudo','-n','find','/etc/kubernetes/manifests','-maxdepth','1','-type','f','-printf','%f\n']))
    containers=command(['sudo','-n','crictl','ps','-a','-o','json'],sanitize=False)
    result['containers']=[]
    if containers['exit']==0:
        try:
            data=json.loads(containers['output'])
            for name in ('kube-apiserver','etcd','setup','etcd-ensure-env-vars','etcd-resources-copy','etcd-cert-syncer'):
                matches=[c for c in data.get('containers',[]) if c.get('metadata',{}).get('name')==name]
                if name=='setup':
                    matches=[c for c in matches if c.get('labels',{}).get('io.kubernetes.pod.namespace')=='openshift-etcd']
                if not matches:
                    result['containers'].append(dict(name=name,state='not observed'))
                    continue
                current=max(matches,key=lambda c:c.get('createdAt',0))
                item=dict(name=name,state=current.get('state'),id=current['id'][:12])
                if name!='kube-apiserver':
                    inspected=command(['sudo','-n','crictl','inspect',current['id']],sanitize=False)
                    if inspected['exit']==0:
                        try:
                            status=json.loads(inspected['output']).get('status',{})
                            item['termination']={key:redact(str(status[key])) for key in ('exitCode','reason','message','startedAt','finishedAt') if key in status}
                        except (ValueError,KeyError):pass
                item['logs']=command(['sudo','-n','crictl','logs','--tail=25',current['id']])
                result['containers'].append(item)
        except (ValueError,KeyError):
            result['container_error']='Container response unavailable or invalid'
    else:
        result['container_error']=redact(containers['output'])
    result['finding']=(findings(result['listeners']['output']) if result['listeners']['exit']==0 else
                       'Listener inspection failed; API/etcd presence is unknown. Verify sudo and tooling.')
    return result


def collect(state,address,compartment):
    ipaddress.IPv4Address(address)
    root=Path(state);config=root/'ssh_config'
    if root.is_symlink() or config.is_symlink() or not config.is_file():
        raise ValueError('Missing or symlink SSH state; run step 01 --ssh-only')
    if root.stat().st_mode & 0o077:
        raise ValueError('Private state directory must restrict group/other access')
    settings=subprocess.run(['ssh','-F',str(config),'-G','okd-target'],capture_output=True,text=True,timeout=10,check=True)
    if not any(line.split()==['hostname',address] for line in settings.stdout.splitlines()):
        raise ValueError('Saved SSH target differs from NODE_IP; rerun step 01 --ssh-only')
    print('Read-only target bootstrap diagnostics over SSH; Kubernetes API is not required. SSH/probe limit: 150s.',flush=True)
    result=subprocess.run(['ssh','-F',str(config),'okd-target',shlex.join(['python3','-','probe',address,compartment])],
                          input=Path(__file__).read_text(),capture_output=True,text=True,timeout=150)
    if result.returncode:
        raise ValueError('Target SSH/bootstrap probe failed. Check VM boot state, SSH identity, account and access; no repair attempted.')
    report=json.loads(result.stdout)
    if not isinstance(report,dict) or report.get('address')!=address or report.get('compartment')!=compartment:
        raise ValueError('Unexpected target diagnostic response')
    report['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    descriptor,path=tempfile.mkstemp(prefix='node-bootstrap-',suffix='.json',dir=root)
    with os.fdopen(descriptor,'w') as stream:
        json.dump(report,stream,indent=2)
    print('FINDING: '+report['finding'],flush=True)
    print('Uptime: '+report.get('uptime',{}).get('output','not collected').strip(),flush=True)
    print('Services: '+report['services']['output'],flush=True)
    print('Static pod files: '+report.get('static_pods',{}).get('output','not collected').strip(),flush=True)
    kubelet_errors=[line for line in report.get('kubelet',{}).get('output','').splitlines()
                    if re.search(r'failed|error|not found|waiting|timed out|refused',line,re.I)]
    focused=[line for line in kubelet_errors if re.search(r'etcd|create.*container|mount|pull.*image',line,re.I)]
    for line in (focused or kubelet_errors)[-5:]:print('Kubelet: '+line[:1000],flush=True)
    for container in report['containers']:
        print(container['name']+': '+str(container['state']),flush=True)
        if container.get('termination'):print('  Status: '+json.dumps(container['termination']),flush=True)
        if 'logs' in container and container['logs'].get('exit') != 0:
            print('  Log collection unavailable; collector errors are not container log evidence. Inspect the private report.',flush=True)
            continue
        errors=[line for line in container.get('logs',{}).get('output','').splitlines()
                if re.search(r'connection refused|timed out|deadline exceeded|error|failed',line,re.I)]
        for line in errors[-2:]:print('  '+line[:350],flush=True)
    print('Private report: '+path,flush=True)
    print('COLLECTION COMPLETE; this is not a cluster-readiness result. No service, certificate, disk or network setting changed.',flush=True)


if __name__=='__main__':
    try:
        if sys.argv[1]=='probe':print(json.dumps(probe(*sys.argv[2:])))
        else:collect(*sys.argv[1:])
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('FAIL: '+str(error),file=sys.stderr)
        sys.exit(1)
