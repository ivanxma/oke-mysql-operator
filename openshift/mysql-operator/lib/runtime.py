"""OpenShift adaptations around the repository's existing ClusterSet implementation."""
import base64
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / 'lib'))
import cluster_config
import clusterset as cs
from label_workers import select_workers

OWNER = 'okd-mysql-scripts'
_original_manifest = cluster_config.build_manifest
_original_validate_existing = cs.validate_existing
cs.ROOT = Path(os.environ.get('MYSQL_STATE_DIR', '.'))


def validate():
    cluster_config.validate()
    pn, pc, rn, rc, _ = cs.settings()
    for ns in (pn, rn, os.environ['OPERATOR_NAMESPACE']):
        cs.require(ns not in ('default', 'kube-system', 'kube-public', 'kube-node-lease')
                   and not ns.startswith(('openshift-', 'kube-')), 'Use dedicated workload namespaces')
    cs.require(len({pn, rn, os.environ['OPERATOR_NAMESPACE']}) == 3, 'Operator, primary and replica namespaces must be distinct')
    cs.require((pn, pc) == (os.environ['MYSQL_NAMESPACE'], os.environ['MYSQL_CLUSTER_NAME']),
               'Primary settings must match MYSQL_NAMESPACE / MYSQL_CLUSTER_NAME')
    for key in ('MYSQL_SERVER_CPU_REQUEST', 'MYSQL_SIDECAR_CPU_REQUEST'):
        value = os.environ.get(key)
        if value is not None:
            cs.require(bool(re.fullmatch(r'(?:[1-9][0-9]*m|[0-9]+(?:\.[0-9]+)?)', value)) and float(value.rstrip('m')) > 0, 'Invalid CPU request: '+key)
    names = os.environ['MYSQL_WORKER_NAMES'].split()
    cs.require(names and len(set(names)) == len(names) and all(re.fullmatch(r'[a-z0-9][a-z0-9.-]*', n) for n in names),
               'Set distinct valid MYSQL_WORKER_NAMES')
    cs.require(os.environ.get('MYSQL_ANTI_AFFINITY') == 'required', 'This worker workflow requires per-cluster anti-affinity')


def guard():
    validate()
    path = os.environ.get('KUBECONFIG', '')
    cs.require(path and Path(path).is_file(), 'Source shared env.sh and select an existing KUBECONFIG')
    config = cs.kubectl('config', 'view', '--minify', '-o', 'json', json_output=True)
    cs.require(config['clusters'][0]['cluster']['server'] == os.environ['MYSQL_EXPECTED_API_SERVER'], 'Wrong cluster API')


def worker_affinity():
    return {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [{'matchExpressions': [
        {'key': 'node-role.kubernetes.io/worker', 'operator': 'Exists'},
        {'key': 'node-role.kubernetes.io/control-plane', 'operator': 'DoesNotExist'},
        {'key': 'node-role.kubernetes.io/master', 'operator': 'DoesNotExist'}]}]}}


def node_selector():
    return {'kubernetes.io/os': 'linux', 'workload': 'mysql-cluster'}


def build_manifest():
    doc = _original_manifest()
    doc['metadata']['labels'] = {'app.kubernetes.io/managed-by': OWNER}
    spec = doc['spec']
    spec['serviceAccountName'] = os.environ['MYSQL_CLUSTER_NAME'] + '-sidecar-sa'
    # Let kubelet/CSI handle ownership; never enable a root chown init container.
    spec['datadirPermissions'] = {'setRightsUsingInitContainer': False, 'fsGroupChangePolicy': 'OnRootMismatch'}
    for container in spec['podSpec']['containers']:
        key = 'MYSQL_SERVER_CPU_REQUEST' if container['name'] == 'mysql' else 'MYSQL_SIDECAR_CPU_REQUEST'
        container['resources']['requests']['cpu'] = os.environ.get(key, container['resources']['requests']['cpu'])
    for pod in (spec['podSpec'], spec['router']['podSpec']):
        pod['nodeSelector'] = node_selector()
        pod.setdefault('affinity', {})['nodeAffinity'] = worker_affinity()
        pod['securityContext'] = {'seccompProfile': {'type': 'RuntimeDefault'}}
    return doc


cluster_config.build_manifest = build_manifest


def validate_existing(existing, manifest):
    _original_validate_existing(existing, manifest)
    if existing:
        for key in ('serviceAccountName', 'datadirPermissions'):
            cs.require(existing['spec'].get(key) == manifest['spec'][key], 'Existing replica security differs: ' + key)
        for actual, expected in ((existing['spec']['podSpec'], manifest['spec']['podSpec']),
                                 (existing['spec']['router']['podSpec'], manifest['spec']['router']['podSpec'])):
            cs.require(actual.get('nodeSelector') == expected['nodeSelector'] and
                       actual.get('affinity', {}).get('nodeAffinity') == expected['affinity']['nodeAffinity'],
                       'Existing replica placement differs; refusing automatic migration')


cs.validate_existing = validate_existing


def workers(document):
    selected = select_workers(document, os.environ['MYSQL_WORKER_NAMES'].split())
    for node in selected:
        cs.require(node['metadata']['labels'].get('workload') == 'mysql-cluster',
                   'Run step 02 to label the configured workers')
    required = max(int(os.environ['MYSQL_INSTANCES']), int(os.environ['REPLICA_MYSQL_INSTANCES']))
    cs.require(len(selected) >= required, f'Need {required} labelled workers for required anti-affinity')
    print(f'Validated {len(selected)} labelled Ready workers; control-plane nodes excluded.')
    return selected


cs.check_workers = workers


def cpu_millicores(value):
    from decimal import Decimal
    text = str(value)
    scale = {'n': Decimal('0.000001'), 'u': Decimal('0.001'), 'm': Decimal(1)}
    return Decimal(text[:-1])*scale[text[-1]] if text[-1:] in scale else Decimal(text)*1000


def pod_cpu_request(pod):
    regular = sum((cpu_millicores(c.get('resources', {}).get('requests', {}).get('cpu', '0'))
                   for c in pod['spec'].get('containers', [])), start=0)
    sidecars = peak = 0
    for c in pod['spec'].get('initContainers', []):
        request = cpu_millicores(c.get('resources', {}).get('requests', {}).get('cpu', '0'))
        if c.get('restartPolicy') == 'Always':
            sidecars += request
            peak = max(peak, sidecars)
        else:
            peak = max(peak, sidecars + request)
    overhead = cpu_millicores(pod['spec'].get('overhead', {}).get('cpu', '0'))
    return max(regular+sidecars, peak)+overhead


def capacity(nodes, pods):
    selected = workers(nodes)
    excluded = {os.environ['MYSQL_NAMESPACE'], os.environ['REPLICA_MYSQL_NAMESPACE'],
                os.environ['OPERATOR_NAMESPACE'], 'oci-csi'}
    # Reserve one member from each cluster and two CSI containers per worker.
    db = 2*(cpu_millicores(os.environ.get('MYSQL_SERVER_CPU_REQUEST', '500m')) +
            cpu_millicores(os.environ.get('MYSQL_SIDECAR_CPU_REQUEST', '100m')))
    spare = 0
    for node in selected:
        name = node['metadata']['name']
        allocated = sum((pod_cpu_request(p) for p in pods['items']
                         if p['spec'].get('nodeName') == name and
                         p['metadata']['namespace'] not in excluded and
                         p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')), start=0)
        available = cpu_millicores(node['status']['allocatable']['cpu']) - allocated
        needed = db+40
        cs.require(available >= needed, f'{name}: available {available}m CPU; need at least {needed}m for two database members and CSI. Adjust private requests or resize workers.')
        spare += available-needed
        print(f'{name}: {available}m CPU available, {needed}m reserved for database members/CSI.')
    routers = int(os.environ['MYSQL_ROUTER_INSTANCES']) + int(os.environ['REPLICA_MYSQL_ROUTER_INSTANCES'])
    cs.require(spare >= 100*(routers+1), 'Insufficient combined CPU for Routers and the operator after database/CSI requests')
    print('CPU request budget fits. Review memory, storage growth and database performance separately.')


def managed(kind, name, namespace=None):
    obj = cs.get(kind, name, namespace, optional=True)
    if obj:
        cs.require(obj['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') == OWNER,
                   'Refusing to adopt an existing unmanaged resource: ' + name)
    return obj


def apply(doc):
    meta = doc['metadata']
    managed(doc['kind'], meta['name'], meta.get('namespace'))
    rendered = json.dumps(doc)
    cs.kubectl('apply', '--dry-run=server', '-f', '-', payload=rendered)
    cs.kubectl('apply', '-f', '-', payload=rendered)


def metadata(name, namespace=None):
    result = {'name': name, 'labels': {'app.kubernetes.io/managed-by': OWNER}}
    if namespace:
        result['namespace'] = namespace
    return result


def scc(uid):
    return {'apiVersion': 'security.openshift.io/v1', 'kind': 'SecurityContextConstraints',
            'metadata': metadata('mysql-okd-uid' + str(uid)),
            'allowPrivilegedContainer': False, 'allowPrivilegeEscalation': False,
            'allowHostDirVolumePlugin': False, 'allowHostIPC': False, 'allowHostNetwork': False,
            'allowHostPID': False, 'allowHostPorts': False, 'readOnlyRootFilesystem': False,
            'requiredDropCapabilities': ['ALL'], 'allowedCapabilities': [],
            'runAsUser': {'type': 'MustRunAs', 'uid': uid},
            'seLinuxContext': {'type': 'MustRunAs'},
            'fsGroup': {'type': 'MustRunAs', 'ranges': [{'min': uid, 'max': uid}]},
            'supplementalGroups': {'type': 'MustRunAs', 'ranges': [{'min': uid, 'max': uid}]},
            'seccompProfiles': ['runtime/default'],
            'volumes': ['configMap', 'downwardAPI', 'emptyDir', 'persistentVolumeClaim', 'projected', 'secret'],
            'users': [], 'groups': []}


def security_documents():
    pn, pc, rn, rc, _ = cs.settings()
    on = os.environ['OPERATOR_NAMESPACE']
    docs = [scc(uid) for uid in (2, 27, 999)]
    for ns, bindings in ((on, [(2, ['mysql-operator-sa'])]),
                          (pn, [(27, [pc+'-sidecar-sa', pc+'-switchover-sa']), (999, [pc+'-sidecar-sa'])]),
                          (rn, [(27, [rc+'-sidecar-sa', rc+'-switchover-sa']), (999, [rc+'-sidecar-sa'])])):
        for uid, accounts in bindings:
            name = 'mysql-use-uid' + str(uid)
            docs += [{'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': metadata(name, ns),
                      'rules': [{'apiGroups': ['security.openshift.io'], 'resources': ['securitycontextconstraints'],
                                 'resourceNames': ['mysql-okd-uid'+str(uid)], 'verbs': ['use']}]},
                     {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': metadata(name, ns),
                      'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name},
                      'subjects': [{'kind': 'ServiceAccount', 'name': sa, 'namespace': ns} for sa in accounts]}]
    return docs


def security():
    pn, _, rn, _, _ = cs.settings()
    docs = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': metadata(ns)}
            for ns in (os.environ['OPERATOR_NAMESPACE'], pn, rn)] + security_documents()
    # Preflight ownership for the complete set before first write.
    for doc in docs:
        managed(doc['kind'], doc['metadata']['name'], doc['metadata'].get('namespace'))
    for doc in docs:
        apply(doc)
    print('Dedicated namespaces and UID-scoped SCC permissions prepared; no anyuid/privileged grant.')


def operator_values():
    return {'deployment': {'namespaces': [os.environ['MYSQL_NAMESPACE'], os.environ['REPLICA_MYSQL_NAMESPACE']],
                           'nodeSelector': node_selector(), 'affinity': {'nodeAffinity': worker_affinity()},
                           'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'memory': '1Gi'}}},
            'envs': {'k8sClusterDomain': os.environ['K8S_CLUSTER_DOMAIN']}}


def includes(actual, expected):
    """Accept server-added defaults, but require every configured field."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and includes(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(includes(a, b) for a, b in zip(actual, expected))
    return actual == expected


def primary():
    workers(cs.get('nodes'))
    doc = build_manifest()
    ns, name = doc['metadata']['namespace'], doc['metadata']['name']
    existing = managed('innodbcluster', name, ns)
    secret = cs.get('secret', os.environ['MYSQL_SECRET_NAME'], ns, optional=True)
    if existing:
        cs.require(secret, 'Restore original missing credentials; never regenerate an existing cluster password')
        for key in ('version', 'instances', 'secretName', 'datadirVolumeClaimTemplate', 'serviceAccountName', 'datadirPermissions', 'podSpec', 'router'):
            cs.require(includes(existing['spec'].get(key), doc['spec'][key]), 'Existing primary spec differs: ' + key)
        cs.credentials(existing)
        print('Existing primary matches; no resources changed.')
        return
    cs.require(not cs.get('statefulset', name, ns, optional=True), 'Orphan StatefulSet exists; refusing bootstrap')
    cs.require(not any(p['metadata']['name'].startswith('datadir-'+name+'-') for p in cs.get('pvc', namespace=ns)['items']),
               'Orphan datadir PVCs exist; use a fresh namespace/name')
    for other in json.loads(cs.kubectl('get', 'innodbclusters', '-A', '-o', 'json'))['items']:
        bases = [int(other['spec'].get('baseServerId', 1000))]
        bases += [int(r['baseServerId']) for r in other['spec'].get('readReplicas', [])]
        cs.require(all(1099 < b or b + 99 < 1000 for b in bases), 'Primary server-ID range 1000-1099 overlaps an existing cluster')
    cs.kubectl('-n', ns, 'apply', '--dry-run=server', '-f', '-', payload=json.dumps(doc))
    if secret:
        cs.credentials(doc)
    else:
        password_file = os.environ.get('MYSQL_ROOT_PASSWORD_FILE')
        if password_file:
            path = Path(password_file)
            cs.require(path.stat().st_mode & 0o077 == 0, 'Restrict MYSQL_ROOT_PASSWORD_FILE to mode 600')
            password = path.read_text().rstrip('\n')
        else:
            cs.require(sys.stdin.isatty(), 'Set MYSQL_ROOT_PASSWORD_FILE for unattended creation')
            password = getpass.getpass('New MySQL root password (12+ characters): ')
            cs.require(password == getpass.getpass('Confirm password: '), 'Passwords differ')
        cs.require(len(password) >= 12 and not any(c in password for c in '\n\r\0'), 'Use a single-line password of at least 12 characters')
        data = {k: base64.b64encode(v.encode()).decode() for k, v in
                {'rootUser': 'root', 'rootHost': '%', 'rootPassword': password}.items()}
        secret_doc = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': metadata(os.environ['MYSQL_SECRET_NAME'], ns),
                      'type': 'Opaque', 'data': data}
        cs.kubectl('create', '-f', '-', payload=json.dumps(secret_doc))
    state = Path(os.environ['MYSQLK8S_GENERATED_DIR'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state/'primary-innodbcluster.json'
    path.write_text(json.dumps(doc, indent=2)+'\n'); path.chmod(0o600)
    cs.kubectl('create', '-f', '-', payload=json.dumps(doc))
    print('Primary submitted in its dedicated namespace; run step 07.')


def storage_errors(namespace, cluster):
    pending = {p['metadata']['uid'] for p in cs.get('pvc', namespace=namespace)['items']
               if p['metadata']['name'].startswith('datadir-'+cluster+'-') and p.get('status', {}).get('phase') != 'Bound'}
    if not pending:
        return
    for event in cs.get('events', namespace=namespace)['items']:
        if event.get('involvedObject', {}).get('uid') not in pending:
            continue
        timestamp = event.get('series', {}).get('lastObservedTime') or event.get('lastTimestamp') or event.get('eventTime')
        if timestamp:
            occurred = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if (datetime.datetime.now(datetime.timezone.utc)-occurred).total_seconds() > 120:
                continue
        message = event.get('message', '')
        cs.require(not any(code in message for code in ('NotAuthorizedOrNotFound', 'Authorization failed', 'Forbidden')),
                   'OCI storage authorization denied for a database PVC. Ask the compartment administrator to grant volume creation/inspection/write and volume attachment/instance attach permissions. CSI retries automatically; rerun verification after the claims bind. No cluster data was deleted.')


def network_check(namespace=None, cluster=None, count=None):
    namespace = namespace or os.environ['MYSQL_NAMESPACE']
    cluster = cluster or os.environ['MYSQL_CLUSTER_NAME']
    count = count or int(os.environ['MYSQL_INSTANCES'])
    pods = cs.kubectl('-n', namespace, 'get', 'pods', '-l',
                      'component=mysqld,mysql.oracle.com/cluster='+cluster, '-o', 'json', json_output=True)['items']
    if len(pods) != count or not all(p.get('status', {}).get('containerStatuses') and
            all(c.get('ready') for c in p['status']['containerStatuses']) for p in pods):
        print('Pod TCP check awaits all database containers running; verification repeats this check before cluster readiness.', flush=True)
        return False
    targets = [(p['metadata']['name'], p['metadata']['name']+'.'+cluster+'-instances.'+namespace+'.svc.'+os.environ['K8S_CLUSTER_DOMAIN']) for p in pods]
    code = ('import json,socket; targets='+repr(targets)+'; result=[]\n'
            'for name,host in targets:\n'
            ' try:\n  s=socket.create_connection((host,3306),3); s.close(); result.append({"target":name,"ok":True})\n'
            ' except Exception as e: result.append({"target":name,"ok":False,"error":type(e).__name__})\n'
            'print("MYSQL_NETWORK_JSON="+json.dumps(result))')
    failures = []
    for pod in pods:
        output = cs.kubectl('--request-timeout=0', '-n', namespace, 'exec', pod['metadata']['name'], '-c', 'sidecar', '--',
                            'mysqlsh', '--py', '--quiet-start=2', '-e', code, timeout=60)
        cs.require('MYSQL_NETWORK_JSON=' in output, 'Pod TCP probe returned no result')
        results = json.JSONDecoder().raw_decode(output.split('MYSQL_NETWORK_JSON=', 1)[1])[0]
        failures.extend(pod['metadata']['name']+' -> '+r['target'] for r in results if not r['ok'])
    cs.require(not failures, 'Database TCP/3306 is blocked: '+', '.join(failures)+
               '. Check NetworkPolicies and node-to-node OVN/Geneve UDP 6081. For the dedicated OCI NSG, run step 03 with --prepare-overlay after its scoped IAM permissions are granted. No shared network rules are changed.')
    print('PASS: all database members can reach each other on TCP/3306 through cluster DNS.', flush=True)
    return True


def wait_cluster(namespace, cluster, count):
    deadline = time.monotonic()+int(os.environ['WAIT_TIMEOUT_SECONDS'])
    network_validated = False
    while True:
        if not network_validated:
            network_validated = network_check(namespace, cluster, count)
        storage_errors(namespace, cluster)
        obj = cs.get('innodbcluster', cluster, namespace)
        cs.require(obj['spec']['instances'] == count, 'Deployed instance count differs from private configuration')
        health = obj.get('status', {}).get('cluster', {})
        print(f"{namespace}/{cluster}: {health.get('status', 'PENDING')} {health.get('onlineInstances', 0)}/{count} ONLINE", flush=True)
        if health.get('status') == 'ONLINE' and health.get('onlineInstances') == count:
            break
        cs.require(time.monotonic() < deadline, 'Timed out waiting for cluster readiness; inspect PVCs, events and operator logs')
        time.sleep(min(10, max(0, deadline-time.monotonic())))
    for resource in ('statefulset/'+cluster, 'deployment/'+cluster+'-router'):
        cs.kubectl('--request-timeout=0', '-n', namespace, 'rollout', 'status', resource,
                   '--timeout='+os.environ['WAIT_TIMEOUT_SECONDS']+'s', timeout=int(os.environ['WAIT_TIMEOUT_SECONDS'])+30)


def verify_primary():
    wait_cluster(os.environ['MYSQL_NAMESPACE'], os.environ['MYSQL_CLUSTER_NAME'], int(os.environ['MYSQL_INSTANCES']))
    print('Primary ONLINE; database and Router rollouts ready.')


def verify_replica():
    wait_cluster(os.environ['REPLICA_MYSQL_NAMESPACE'], os.environ['REPLICA_MYSQL_CLUSTER_NAME'], int(os.environ['REPLICA_MYSQL_INSTANCES']))
    cs.verify()


def verify_placement():
    selected = {n['metadata']['name'] for n in workers(cs.get('nodes'))}
    targets = [(os.environ['MYSQL_NAMESPACE'], os.environ['MYSQL_CLUSTER_NAME']),
               (os.environ['REPLICA_MYSQL_NAMESPACE'], os.environ['REPLICA_MYSQL_CLUSTER_NAME'])]
    if os.environ.get('MYSQL_VERIFY_PRIMARY_ONLY') == 'true':
        targets = targets[:1]
    for ns, cluster in targets:
        pods = json.loads(cs.kubectl('-n', ns, 'get', 'pods', '-l', 'mysql.oracle.com/cluster='+cluster, '-o', 'json'))['items']
        cs.require(pods, 'No database/Router pods found')
        for pod in pods:
            cs.require(pod['spec'].get('nodeName') in selected, 'Pod is not on an intended worker: '+pod['metadata']['name'])
            cs.require(any(c['type']=='Ready' and c['status']=='True' for c in pod.get('status', {}).get('conditions', [])),
                       'Pod is not Ready: '+pod['metadata']['name'])
    print('Database and Router pods run only on configured labelled worker nodes.')


def replication_test():
    import uuid
    pn, pc, rn, rc, _ = cs.settings()
    data = cs.credentials(cs.get('innodbcluster', pc, pn))
    token = uuid.uuid4().hex
    # Write through the primary Router; do not assume pod ordinal 0 is the writer.
    sql = ('CREATE DATABASE IF NOT EXISTS mysql_operator_validation; '
           'CREATE TABLE IF NOT EXISTS mysql_operator_validation.replication_probe '
           '(id VARCHAR(32) PRIMARY KEY, written_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP); '
           f"INSERT INTO mysql_operator_validation.replication_probe(id) VALUES ('{token}');")
    primary_code = 's=shell.get_session(); ' + '; '.join('s.run_sql('+repr(q.strip())+')' for q in sql.split(';') if q.strip())
    def execute(ns, name, code, uri='root@127.0.0.1:3306'):
        return cs.kubectl('--request-timeout=0', '-n', ns, 'exec', '-i', name+'-0', '-c', 'sidecar', '--',
                          'mysqlsh', '--uri', uri, '--passwords-from-stdin', '--py', '--quiet-start=2',
                          '-e', code, payload=base64.b64decode(data['rootPassword']).decode()+'\n', timeout=90)
    execute(pn, pc, primary_code, f"root@{pc}.{pn}.svc.{os.environ['K8S_CLUSTER_DOMAIN']}:3306")
    query = f"SELECT COUNT(*) FROM mysql_operator_validation.replication_probe WHERE id='{token}'"
    table_query = "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='mysql_operator_validation' AND table_name='replication_probe'"
    code = 's=shell.get_session(); exists=s.run_sql('+repr(table_query)+').fetch_one()[0]; print("REPLICATION_PROBE="+str(s.run_sql('+repr(query)+').fetch_one()[0] if exists else 0))'
    deadline = time.monotonic()+int(os.environ['WAIT_TIMEOUT_SECONDS'])
    while True:
        if 'REPLICATION_PROBE=1' in execute(rn, rc, code):
            print('PASS: primary write observed on replica; validation row retained for audit.')
            return
        cs.require(time.monotonic()<deadline, 'Replication probe timed out')
        time.sleep(5)


def main():
    action = sys.argv[1]
    validate()
    if action == 'validate':
        return
    if action == 'workers':
        workers(json.load(sys.stdin)); return
    if action == 'manifest':
        print(json.dumps(build_manifest(), indent=2)); return
    if action == 'operator-values':
        print(json.dumps(operator_values(), indent=2)); return
    guard()
    {'capacity': lambda: capacity(cs.get('nodes'), cs.kubectl('get', 'pods', '-A', '-o', 'json', json_output=True)), 'security': security, 'primary': primary, 'replica': lambda: cs.create(verify_script='09-verify-clusterset.sh'), 'check-replica': cs.check,
     'network-check': network_check, 'verify-primary': verify_primary, 'verify': verify_replica, 'placement': verify_placement, 'replication-test': replication_test}[action]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, cs.SetupError, subprocess.SubprocessError, OSError) as error:
        sys.exit('ERROR: '+str(error))
