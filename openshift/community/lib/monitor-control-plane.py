"""Stream installer wait output and show regular read-only readiness snapshots."""
import datetime
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time


def redact(text):
    return re.sub(r'(?i)(password\s*:\s*).*', r'\1[read protected auth file]', text).rstrip()


def emit(message):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S UTC')
    print('['+stamp+'] '+redact(message), flush=True)


def execute(command, env, limit=6):
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=limit)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, '', str(error)


def summarize(nodes, operators):
    ready_nodes = sum(any(c.get('type')=='Ready' and c.get('status')=='True'
                         for c in node.get('status',{}).get('conditions',[])) for node in nodes)
    available = progressing = degraded = 0
    pending = []
    for operator in operators:
        conditions = {c['type']:c.get('status') for c in operator.get('status',{}).get('conditions',[])}
        available += conditions.get('Available')=='True'
        progressing += conditions.get('Progressing')=='True'
        degraded += conditions.get('Degraded')=='True'
        if conditions.get('Available')!='True' or conditions.get('Progressing')!='False' or conditions.get('Degraded')!='False':
            pending.append(operator.get('metadata',{}).get('name','unknown'))
    settled = bool(nodes and operators) and ready_nodes == len(nodes) and not pending
    messages = ['Nodes Ready: %s/%s' % (ready_nodes,len(nodes)),
                'Operators: %s/%s Available, %s Progressing, %s Degraded' % (available,len(operators),progressing,degraded),
                'Waiting for operators: '+', '.join(pending) if pending else
                ('Operators settled.' if operators else 'Waiting for ClusterOperator objects to appear.')]
    for operator in operators:
        name = operator.get('metadata',{}).get('name','unknown')
        if name not in ('kube-apiserver','etcd','ingress','authentication') or name not in pending:
            continue
        conditions = operator.get('status',{}).get('conditions',[])
        for condition_type, status in (('Degraded','True'),('Available','False'),('Progressing','True')):
            condition = next((c for c in conditions if c.get('type')==condition_type and c.get('status')==status), None)
            if condition:
                message = condition.get('message','').splitlines()
                detail = message[0][:220] if message else 'condition pending'
                messages.append('WAITING '+name+': '+condition.get('reason',condition_type)+' — '+detail)
                break
    return settled, messages


def snapshot(oc, env, domain, address):
    emit('STATUS: checking DNS and API readiness...')
    rc, output, _ = execute(['getent','ahostsv4','api.'+domain],env,4)
    if rc or address not in {line.split()[0] for line in output.splitlines() if line.split()}:
        emit('WAITING: API DNS does not resolve to the registered node. Run 05-verify-dns.sh.')
        return False
    emit('DNS: API resolves to the registered node.')
    command = [str(oc),'--request-timeout=4s']
    rc, output, error = execute(command+['get','--raw=/readyz'],env)
    if rc or output.strip()!='ok':
        detail = (error.strip() or output.strip() or 'API not ready').splitlines()[0][:240]
        emit('WAITING: Kubernetes API readiness — '+detail)
        return False
    emit('API: readyz=ok; inspecting nodes and cluster operators...')
    data = []
    for resource in ('nodes','clusteroperators'):
        emit('Querying '+resource+' (API request timeout 30s; command limit 40s)...')
        rc, output, error = execute([str(oc),'--request-timeout=30s','get',resource,'-o','json'],env,40)
        if rc:
            emit('WAITING: cannot read '+resource+' — '+(error.strip() or 'request failed').splitlines()[0][:240])
            emit('API readyz passed, but resource inspection failed; cluster completion is NOT confirmed.')
            emit('If this repeats, run 05-diagnose-control-plane.sh for read-only bootstrap evidence before attempting recovery.')
            return False
        try:
            data.append(json.loads(output)['items'])
        except (ValueError,KeyError):
            emit('WAITING: invalid '+resource+' response')
            return False
    settled, messages = summarize(*data)
    for message in messages:
        emit(message)
    emit('Read-only readiness snapshot passed; step 06 still checks full acceptance.' if settled else
         'WAITING: node/operator readiness. Bootstrap continues on the VM.')
    return settled


def monitor(work, selected, domain, address, interval, timeout, mode):
    interval, timeout = int(interval), int(timeout)
    if not 5 <= interval <= 300 or not 1 <= timeout <= 86400 or mode not in ('wait','status'):
        raise ValueError('Use --interval 5..300 and --timeout 1..86400 seconds')
    root = Path(work)
    kubeconfig = Path(selected)
    if not kubeconfig.is_absolute() or not kubeconfig.is_file() or not kubeconfig.samefile(root/'sno/auth/kubeconfig'):
        raise ValueError('KUBECONFIG must select the protected auth file for this installation')
    if (root/'cluster-domain').read_text().strip()!=domain:
        raise ValueError('Workspace cluster domain differs from selected configuration')
    env = dict(os.environ, KUBECONFIG=str(kubeconfig), PATH=str(root/'bin')+os.pathsep+os.environ.get('PATH',''))
    oc = root/'bin/oc'
    rc, output, _ = execute([str(oc),'config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}'],env)
    if rc or output.strip()!='https://api.'+domain+':6443':
        raise ValueError('KUBECONFIG API identity mismatch; refusing to monitor another cluster')
    if mode=='status':
        emit('ONE-SHOT STATUS CHECK for '+domain+' — read-only; does not wait for installation to finish.')
        ready = snapshot(oc,env,domain,address)
        emit('RESULT: READINESS SNAPSHOT PASSED — installer completion still requires the normal wait command.' if ready else
             'RESULT: INSTALLATION NOT READY — completion is NOT confirmed; readiness checks have not all passed.')
        emit('The status-only check has finished. Returning to the shell does NOT mean installation completed.')
        emit('NEXT: run 05-wait-for-control-plane.sh WITHOUT --status-only to wait for completion, then run 06-check-control-plane.sh.')
        return 0 if ready else 1
    emit('Read-only installation monitoring started for '+domain+'.')
    emit('Ctrl-C stops this monitor only; no VM, DNS or cluster configuration is changed.')
    emit('Status interval: %ss; monitoring timeout: %ss. Installer messages stream as they arrive.' % (interval,timeout))
    installer = subprocess.Popen([str(root/'bin/openshift-install'),'--dir',str(root/'sno'),
                                  'wait-for','install-complete','--log-level=info'],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1, start_new_session=True)
    lines = queue.Queue()
    def read_output():
        for line in installer.stdout:
            lines.put(line)
    reader = threading.Thread(target=read_output,daemon=True)
    reader.start()
    start = time.monotonic(); next_status = start
    try:
        while True:
            now = time.monotonic()
            if now-start >= timeout:
                emit('RESULT: MONITOR TIMEOUT — installation completion is NOT confirmed. Bootstrap may still be running; inspect status or resume monitoring.')
                return 124
            if now>=next_status:
                emit('Elapsed: %ss. Installer is waiting for install-complete.' % int(now-start))
                snapshot(oc,env,domain,address)
                next_status = time.monotonic()+interval
            try:
                emit('INSTALLER: '+lines.get(timeout=1))
            except queue.Empty:
                pass
            if installer.poll() is not None:
                reader.join(timeout=1)
                while not lines.empty():
                    emit('INSTALLER: '+lines.get_nowait())
                code = installer.returncode
                emit('RESULT: INSTALLER COMPLETED — installer reports install-complete. Next: 06-check-control-plane.sh for final acceptance.' if code==0 else
                     'RESULT: WAIT FAILED (exit %s) — installation completion is NOT confirmed. Inspect the messages above; no repair or reinstallation was attempted.' % code)
                return code
    finally:
        if installer.poll() is None:
            os.killpg(installer.pid,signal.SIGTERM)
            try:
                installer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(installer.pid,signal.SIGKILL)
                installer.wait()
        installer.stdout.close()


if __name__ == '__main__':
    def interrupted(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGHUP,interrupted)
    try:
        sys.exit(monitor(*sys.argv[1:]))
    except KeyboardInterrupt:
        emit('Monitoring stopped. The VM installation has not been stopped.')
        sys.exit(130)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        emit('FAIL: '+str(error))
        sys.exit(1)
