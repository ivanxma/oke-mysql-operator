"""Scoped OCI Block CSI preparation for the numbered OKD MySQL workflow."""
import copy
import hashlib
import json
import os
import time
from pathlib import Path
import urllib.request

import oci
import yaml
import runtime as rt
from runtime import cs

COMMIT = '7f962db6d99dddc89f90a6a2265577b11feb3a21'
MANIFEST_SHA256 = 'f3af5c329e11a2272836856d29f3cc6f0af852ec0e84c79850b3c6538a858803'
OWNER = 'okd-mysql-oci-storage'
NAMESPACE = 'oci-csi'


def clients():
    cid = os.environ['MYSQL_OCI_COMPARTMENT_ID']
    name = os.environ['MYSQL_OCI_COMPARTMENT_NAME']
    region = os.environ['MYSQL_OCI_REGION']
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    options = dict(signer=signer, timeout=(5, 30), retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    identity = oci.identity.IdentityClient(config, **options)
    compartment = identity.get_compartment(cid).data
    cs.require(compartment.name == name and compartment.lifecycle_state == 'ACTIVE', 'Compartment identity mismatch')
    return cid, name, region, oci.core.ComputeClient(config, **options), oci.core.VirtualNetworkClient(config, **options)


def scope():
    cid, name, region, compute, network = clients()
    nodes = cs.get('nodes')['items']
    workers = rt.workers({'items': nodes})
    selected = workers + [n for n in nodes if 'node-role.kubernetes.io/control-plane' in n['metadata']['labels']]
    ips = {next(a['address'] for a in n['status']['addresses'] if a['type'] == 'InternalIP'): n for n in selected}
    found = []
    attachments = oci.pagination.list_call_get_all_results(compute.list_vnic_attachments, compartment_id=cid).data
    for attachment in attachments:
        if attachment.compartment_id != cid or attachment.lifecycle_state != 'ATTACHED':
            continue
        vnic = network.get_vnic(attachment.vnic_id).data
        if vnic.private_ip not in ips:
            continue
        instance = compute.get_instance(attachment.instance_id).data
        cs.require(instance.compartment_id == cid and instance.lifecycle_state == 'RUNNING', 'Node instance scope/state mismatch')
        subnet = network.get_subnet(vnic.subnet_id).data
        found.append(dict(node=ips[vnic.private_ip], instance_id=instance.id, compartment_id=cid,
                          availability_domain=instance.availability_domain, vcn_id=subnet.vcn_id, region=region))
    cs.require(len(found) == len(selected) and len({x['instance_id'] for x in found}) == len(selected),
               'Cannot uniquely map all selected nodes to running instances in the allowed compartment')
    cs.require(len({x['vcn_id'] for x in found}) == 1, 'Node VCN selection is ambiguous')
    return dict(compartment_id=cid, compartment_name=name, region=region, vcn_id=found[0]['vcn_id'], nodes=found)


def meta(name, namespace=None):
    value = {'name': name, 'labels': {'app.kubernetes.io/managed-by': OWNER}}
    if namespace:
        value['namespace'] = namespace
    return value


def transform(source, inventory):
    """Keep Block Volume only, without changing another default storage class."""
    retained = []
    for original in source:
        if not original:
            continue
        d = copy.deepcopy(original)
        kind, name = d['kind'], d['metadata']['name']
        if kind in ('StorageClass', 'VolumeSnapshotClass') or name == 'fss.csi.oraclecloud.com':
            continue
        if name == 'oci-fss-csi':
            continue
        d['metadata'].setdefault('labels', {})['app.kubernetes.io/managed-by'] = OWNER
        if kind == 'Namespace':
            # Never use the run-level label that bypasses admission checks.
            d['metadata']['labels'].pop('openshift.io/run-level', None)
            d['metadata']['labels'].pop('pod-security.kubernetes.io/enforce-version', None)
        if kind == 'Deployment':
            pod = d['spec']['template']['spec']
            pod['containers'] = [c for c in pod['containers'] if c['name'] not in
                                 ('csi-fss-volume-provisioner', 'snapshot-controller', 'csi-snapshotter')]
            pod.pop('imagePullSecrets', None)
            # Host-network CSI must use the node resolver for OCI API/auth DNS.
            pod['dnsPolicy'] = 'Default'
            for c in pod['containers']:
                c['resources'] = {'requests': {'cpu': '20m', 'memory': '64Mi'}}
            d['spec']['replicas'] = 1
            d['spec']['minReadySeconds'] = 35
            for c in pod['containers']:
                if c['name'] == 'oci-csi-controller-driver':
                    c['readinessProbe'] = {'exec': {'command': ['/bin/sh', '-c', 'test -S /var/run/shared-tmpfs/csi.sock']},
                                           'periodSeconds': 5, 'timeoutSeconds': 2}
        if kind == 'DaemonSet':
            d['spec']['minReadySeconds'] = 30
            pod = d['spec']['template']['spec']
            pod['nodeSelector'] = rt.node_selector()
            pod['affinity'] = {'nodeAffinity': rt.worker_affinity()}
            pod['containers'] = [c for c in pod['containers'] if c['name'] != 'csi-node-registrar-fss']
            pod.pop('imagePullSecrets', None)
            for c in pod['containers']:
                c['resources'] = {'requests': {'cpu': '20m', 'memory': '64Mi'}}
                if c['name'] == 'oci-csi-node-driver':
                    c['args'] = [a for a in c['args'] if not a.startswith('--fss-endpoint=')] + ['--fss-csi-driver-enabled=false']
                    c['volumeMounts'] = [m for m in c['volumeMounts'] if m['name'] not in ('fss-plugin-dir', 'fss-driver-mounts')]
            pod['volumes'] = [v for v in pod['volumes'] if v['name'] not in ('fss-plugin-dir', 'fss-driver-mounts')]
        if kind == 'CSIDriver':
            d.setdefault('spec', {})['fsGroupPolicy'] = 'File'
        if kind == 'ClusterRole':
            d['rules'] = [r for r in d['rules'] if 'snapshot.storage.k8s.io' not in r.get('apiGroups', [])]
        retained.append(d)
    # A dedicated CSI-only account needs host mounts for the storage node plugin.
    # This grant never applies to the MySQL database/operator service accounts.
    scc_name = 'mysql-oci-csi-driver'
    retained += [
        {'apiVersion': 'security.openshift.io/v1', 'kind': 'SecurityContextConstraints', 'metadata': meta(scc_name),
         'allowPrivilegedContainer': True, 'allowPrivilegeEscalation': True, 'allowHostDirVolumePlugin': True,
         'allowHostIPC': False, 'allowHostNetwork': True, 'allowHostPID': False, 'allowHostPorts': False,
         'readOnlyRootFilesystem': False, 'allowedCapabilities': ['*'], 'volumes': ['*'],
         'runAsUser': {'type': 'RunAsAny'}, 'seLinuxContext': {'type': 'RunAsAny'},
         'fsGroup': {'type': 'RunAsAny'}, 'supplementalGroups': {'type': 'RunAsAny'},
         'seccompProfiles': ['*'], 'users': [], 'groups': []},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': meta('use-mysql-oci-csi', NAMESPACE),
         'rules': [{'apiGroups': ['security.openshift.io'], 'resources': ['securitycontextconstraints'],
                    'resourceNames': [scc_name], 'verbs': ['use']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': meta('use-mysql-oci-csi', NAMESPACE),
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'use-mysql-oci-csi'},
         'subjects': [{'kind': 'ServiceAccount', 'name': 'csi-oci-node-sa', 'namespace': NAMESPACE}]},
        {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': meta('oci-volume-provisioner', NAMESPACE),
         'stringData': {'config.yaml': json.dumps({'useInstancePrincipals': True, 'compartment': inventory['compartment_id'],
            'vcn': inventory['vcn_id'], 'region': inventory['region'],
            'loadBalancer': {'securityListManagementMode': 'None'},
            'rateLimiter': {'rateLimitQPSRead': 20.0, 'rateLimitBucketRead': 5,
                            'rateLimitQPSWrite': 20.0, 'rateLimitBucketWrite': 5}})}},
        {'apiVersion': 'storage.k8s.io/v1', 'kind': 'StorageClass', 'metadata': meta('mysql-oci-bv'),
         'provisioner': 'blockvolume.csi.oraclecloud.com', 'allowVolumeExpansion': True,
         'reclaimPolicy': 'Retain', 'volumeBindingMode': 'WaitForFirstConsumer',
         'parameters': {'attachment-type': 'paravirtualized', 'vpusPerGB': '10', 'csi.storage.k8s.io/fstype': 'ext4'}}]
    return retained


def prepare_nodes(inventory):
    planned = []
    for item in inventory['nodes']:
        node = cs.get('node', item['node']['metadata']['name'])
        provider = 'oci://' + item['instance_id']
        cs.require(node['metadata']['uid'] == item['node']['metadata']['uid'], 'Node identity changed')
        cs.require(node['spec'].get('providerID') in (None, '', provider), 'Existing providerID conflicts with verified OCI instance')
        annotations = node['metadata'].get('annotations', {})
        key = 'oci.oraclecloud.com/compartment-id'
        cs.require(annotations.get(key) in (None, inventory['compartment_id']), 'Existing compartment annotation conflicts')
        zone = item['availability_domain'].split(':')[-1]
        labels = {'topology.kubernetes.io/region': inventory['region'], 'topology.kubernetes.io/zone': zone,
                  'failure-domain.beta.kubernetes.io/region': inventory['region'], 'failure-domain.beta.kubernetes.io/zone': zone}
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': node['metadata']['uid']},
                 {'op': 'test', 'path': '/metadata/resourceVersion', 'value': node['metadata']['resourceVersion']}]
        if not node['spec'].get('providerID'):
            patch.append({'op': 'add', 'path': '/spec/providerID', 'value': provider})
        if 'annotations' not in node['metadata']:
            patch.append({'op': 'add', 'path': '/metadata/annotations', 'value': {}})
        if annotations.get(key) is None:
            patch.append({'op': 'add', 'path': '/metadata/annotations/oci.oraclecloud.com~1compartment-id', 'value': inventory['compartment_id']})
        for key, value in labels.items():
            current = node['metadata']['labels'].get(key)
            cs.require(current in (None, value), 'Existing node topology conflicts with OCI inventory')
            if current is None:
                patch.append({'op': 'add', 'path': '/metadata/labels/'+key.replace('/', '~1'), 'value': value})
        if len(patch) > 2:
            args = ['patch', 'node', node['metadata']['name'], '--type=json', '-p', json.dumps(patch)]
            cs.kubectl(*args, '--dry-run=server')
            planned.append(args)
    for args in planned:
        cs.kubectl(*args)


def audit():
    """Read-only verification of the actual volumes and worker attachments."""
    rt.guard()
    inventory = scope()
    cid, _, region, compute, _ = clients()
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    storage = oci.core.BlockstorageClient({'region': region}, signer=signer, timeout=(5, 30),
                                        retry_strategy=oci.retry.NoneRetryStrategy())
    worker_ids = {item['instance_id'] for item in inventory['nodes']
                  if item['node']['metadata']['labels'].get('workload') == 'mysql-cluster' and
                  not any(k in item['node']['metadata']['labels'] for k in
                          ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master'))}
    report = []
    for namespace, cluster, count in ((os.environ['MYSQL_NAMESPACE'], os.environ['MYSQL_CLUSTER_NAME'], int(os.environ['MYSQL_INSTANCES'])),
                                      (os.environ['REPLICA_MYSQL_NAMESPACE'], os.environ['REPLICA_MYSQL_CLUSTER_NAME'], int(os.environ['REPLICA_MYSQL_INSTANCES']))):
        claims = [p for p in cs.get('pvc', namespace=namespace)['items']
                  if p['metadata']['name'].startswith('datadir-'+cluster+'-')]
        cs.require(len(claims) == count, 'Unexpected database PVC count')
        for claim in claims:
            cs.require(claim['status']['phase'] == 'Bound', 'Database PVC is not Bound')
            pv = cs.get('pv', claim['spec']['volumeName'])
            cs.require(pv['spec']['claimRef']['uid'] == claim['metadata']['uid'], 'PV claim identity mismatch')
            cs.require(pv['spec']['csi']['driver'] == 'blockvolume.csi.oraclecloud.com', 'Unexpected database CSI driver')
            volume = storage.get_volume(pv['spec']['csi']['volumeHandle']).data
            cs.require(volume.compartment_id == cid and volume.lifecycle_state == 'AVAILABLE', 'Database volume compartment/state mismatch')
            attachments = oci.pagination.list_call_get_all_results(compute.list_volume_attachments, compartment_id=cid, volume_id=volume.id).data
            active = [a for a in attachments if a.lifecycle_state == 'ATTACHED']
            cs.require(len(active) == 1 and active[0].compartment_id == cid and active[0].instance_id in worker_ids,
                       'Database volume is not exclusively attached to an authorized worker')
            report.append({'namespace': namespace, 'pvc': claim['metadata']['name'], 'pv': pv['metadata']['name'],
                           'volume_id': volume.id, 'compartment_id': cid, 'size_gib': volume.size_in_gbs,
                           'worker_instance_id': active[0].instance_id})
    path = Path(os.environ['MYSQL_STATE_DIR'])/'oci-storage/volume-audit.json'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(report, indent=2)+'\n'); path.chmod(0o600)
    print(f'Verified {len(report)} database volumes in the authorized compartment; all attached to selected workers.')


def worker_device_config(source):
    workers = [d for d in source if d and d.get('kind') == 'MachineConfig' and
               d['metadata'].get('labels', {}).get('machineconfiguration.openshift.io/role') == 'worker']
    cs.require(len(workers) == 1, 'Pinned OCI worker device configuration is ambiguous')
    doc = copy.deepcopy(workers[0])
    doc['metadata']['name'] = '99-mysql-oci-consistent-device-path'
    doc['metadata']['labels']['app.kubernetes.io/managed-by'] = OWNER
    paths = {f['path'] for f in doc['spec']['config']['storage']['files']}
    cs.require(paths == {'/etc/udev/rules.d/99-systemoci-persistent-names.rules', '/etc/ociudevpersistentnaming'},
               'Unexpected files in pinned OCI device configuration')
    return doc


def worker_pool_scope(inventory):
    selected = {n['node']['metadata']['name'] for n in inventory['nodes']
                if n['node']['metadata']['labels'].get('workload') == 'mysql-cluster'}
    actual = {n['metadata']['name'] for n in cs.get('nodes')['items']
              if 'node-role.kubernetes.io/worker' in n['metadata']['labels'] and
              not any(k in n['metadata']['labels'] for k in
                      ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master'))}
    pool = cs.get('machineconfigpool', 'worker')
    cs.require(selected == actual and pool.get('status', {}).get('machineCount') == len(selected),
               'Worker MachineConfig would affect nodes outside the verified worker set')
    return len(selected)


def wait_worker_config(name, count):
    deadline = time.monotonic()+int(os.environ['WAIT_TIMEOUT_SECONDS'])
    while True:
        pool = cs.get('machineconfigpool', 'worker')
        status = pool.get('status', {})
        includes = any(x['name'] == name for x in status.get('configuration', {}).get('source', []))
        updated = any(c['type'] == 'Updated' and c['status'] == 'True' for c in status.get('conditions', []))
        ready = includes and updated and status.get('updatedMachineCount') == count and status.get('readyMachineCount') == count
        print(f"OCI worker device configuration: {status.get('updatedMachineCount', 0)}/{count} updated, {status.get('readyMachineCount', 0)}/{count} ready", flush=True)
        if ready:
            return
        cs.require(not status.get('degradedMachineCount', 0), 'Worker MachineConfig rollout degraded; inspect MCO before continuing')
        cs.require(time.monotonic() < deadline, 'Timed out waiting for OCI worker device configuration')
        time.sleep(10)


def main():
    rt.guard()
    state = Path(os.environ['MYSQL_STATE_DIR'])/'oci-storage'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    inventory = scope()
    if os.environ.get('MYSQL_OCI_PREPARE_OVERLAY') == 'true':
        from oci_network import prepare
        _, _, _, compute, network = clients()
        prepare(inventory, compute, network, cs.get('namespace', 'kube-system')['metadata']['uid'], state)
    path = state/'scope.json'; path.write_text(json.dumps(inventory, indent=2)+'\n'); path.chmod(0o600)
    url = f'https://raw.githubusercontent.com/oracle-quickstart/oci-openshift/{COMMIT}/custom_manifests/manifests/01-oci-csi.yml'
    data = urllib.request.urlopen(url, timeout=30).read()
    cs.require(hashlib.sha256(data).hexdigest() == MANIFEST_SHA256, 'Pinned OCI source manifest checksum mismatch')
    docs = transform(list(yaml.safe_load_all(data)), inventory)
    device_url = f'https://raw.githubusercontent.com/oracle-quickstart/oci-openshift/{COMMIT}/custom_manifests/manifests/03-machineconfig-consistent-device-path.yml'
    device_data = urllib.request.urlopen(device_url, timeout=30).read()
    cs.require(hashlib.sha256(device_data).hexdigest() == 'd656a68b56c941809088667875c4f24d7da478dcc9f932b1304224498a3ab116', 'Pinned OCI device manifest checksum mismatch')
    device_doc = worker_device_config(list(yaml.safe_load_all(device_data)))
    worker_count = worker_pool_scope(inventory)
    docs.append(device_doc)
    # Fail closed if any target resource is managed by another installer.
    for d in docs:
        old = cs.get(d['kind'], d['metadata']['name'], d['metadata'].get('namespace'), optional=True)
        cs.require(not old or old['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') == OWNER,
                   'Existing CSI resource requires explicit review: '+d['metadata']['name'])
    prepare_nodes(inventory)
    ordered = sorted(docs, key=lambda x: {'Namespace': 0, 'SecurityContextConstraints': 1, 'ServiceAccount': 2,
                     'ClusterRole': 3, 'ClusterRoleBinding': 4, 'Role': 5, 'RoleBinding': 6, 'Secret': 7,
                     'ConfigMap': 8, 'CSIDriver': 9, 'MachineConfig': 10, 'Deployment': 11, 'DaemonSet': 11, 'StorageClass': 12}.get(x['kind'], 20))
    for d in ordered:
        value = json.dumps(d)
        cs.kubectl('apply', '--dry-run=server', '-f', '-', payload=value)
        cs.kubectl('apply', '-f', '-', payload=value)
    wait_worker_config(device_doc['metadata']['name'], worker_count)
    for resource in ('deployment/csi-oci-controller', 'daemonset/csi-oci-node'):
        cs.kubectl('--request-timeout=0', '-n', NAMESPACE, 'rollout', 'status', resource, '--timeout=600s', timeout=630)
    print('OCI Block CSI installed; mysql-oci-bv prepared with Retain and no default-class change.')


if __name__ == '__main__':
    import sys
    try:
        if sys.argv[1:] == ['--audit']:
            audit()
        else:
            main()
    except oci.exceptions.ServiceError as error:
        sys.exit(f'ERROR: OCI API failed: {error.status} {error.code}. Check the scoped instance-principal permissions; review the private deployment log/state before retrying. No alternate credentials are used.')
    except (ValueError, cs.SetupError) as error:
        sys.exit('ERROR: '+str(error))
