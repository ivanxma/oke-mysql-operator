"""OpenShift MySQL placement, SCC, cluster identity and bootstrap safeguards."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import types
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'openshift/mysql-operator'
sys.path.insert(0, str(ROOT/'lib'))
sys.path.insert(0, str(DIR/'lib'))
spec = importlib.util.spec_from_file_location('okd_mysql_runtime', DIR/'lib/runtime.py')
rt = importlib.util.module_from_spec(spec)
# Loading the adapter temporarily replaces hooks in shared modules; restore them
# so OKE tests and scripts retain their original behavior in the same process.
import cluster_config
import clusterset
original_build = cluster_config.build_manifest
original_existing = clusterset.validate_existing
original_workers = clusterset.check_workers
original_root = clusterset.ROOT
spec.loader.exec_module(rt)
cluster_config.build_manifest = original_build
clusterset.validate_existing = original_existing
clusterset.check_workers = original_workers
clusterset.ROOT = original_root


storage_spec = importlib.util.spec_from_file_location('okd_mysql_storage', DIR/'lib/oci_storage.py')
storage = importlib.util.module_from_spec(storage_spec)
with patch.dict(sys.modules, {'oci': types.ModuleType('oci'), 'yaml': types.ModuleType('yaml'), 'runtime': rt}):
    storage_spec.loader.exec_module(storage)

network_spec = importlib.util.spec_from_file_location('okd_mysql_network', DIR/'lib/oci_network.py')
network_adapter = importlib.util.module_from_spec(network_spec)
with patch.dict(sys.modules, {'oci': types.ModuleType('oci')}):
    network_spec.loader.exec_module(network_adapter)


def node(name, control=False, ready=True, workload='mysql-cluster'):
    labels = {'node-role.kubernetes.io/worker': '', 'kubernetes.io/os': 'linux', 'kubernetes.io/arch': 'amd64'}
    if workload is not None: labels['workload'] = workload
    if control: labels['node-role.kubernetes.io/control-plane'] = ''
    return {'metadata': {'name': name, 'labels': labels, 'uid': name, 'resourceVersion': '1'},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}]}}


class OpenShiftMySQLTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.cfg = self.path/'private.env'
        self.cfg.write_text((DIR/'config.env.example').read_text())
        shared = self.path/'okd.env'
        shared.write_text('ADMIN_WORKDIR='+str(self.path)+'\n')
        result = subprocess.run(['bash', '-c', 'source "$1/lib/common.sh"; python3 -c "import os,json; print(json.dumps(dict(os.environ)))"', '--', str(DIR)],
                                env={**os.environ, 'MYSQL_OPENSHIFT_CONFIG': str(self.cfg), 'OKD_CONFIG_FILE': str(shared)},
                                text=True, capture_output=True, check=True)
        self.environment = patch.dict(os.environ, json.loads(result.stdout), clear=True)
        self.environment.start(); self.addCleanup(self.environment.stop)

    def test_primary_and_router_require_label_and_exclude_control_plane(self):
        doc = rt.build_manifest()
        for pod in (doc['spec']['podSpec'], doc['spec']['router']['podSpec']):
            self.assertEqual(pod['nodeSelector']['workload'], 'mysql-cluster')
            terms = pod['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
            expressions = {e['key']: e['operator'] for e in terms[0]['matchExpressions']}
            self.assertEqual(expressions['node-role.kubernetes.io/worker'], 'Exists')
            self.assertEqual(expressions['node-role.kubernetes.io/control-plane'], 'DoesNotExist')
            self.assertEqual(expressions['node-role.kubernetes.io/master'], 'DoesNotExist')
        self.assertFalse(doc['spec']['datadirPermissions']['setRightsUsingInitContainer'])
        self.assertEqual(doc['spec']['instances'], 3)

    def test_replica_inherits_worker_placement_and_native_bootstrap(self):
        primary = rt.build_manifest()
        with patch.object(cluster_config, 'build_manifest', rt.build_manifest):
            replica = rt.cs.build_replica(primary, 'test-csi')
        self.assertEqual(replica['metadata']['namespace'], 'mysql-secondary')
        self.assertEqual(replica['spec']['baseServerId'], 2000)
        self.assertIn('mycluster-instances.mysql.svc.cluster.local', replica['spec']['initDB']['clusterSet']['targetUrl'])
        self.assertEqual(replica['spec']['podSpec']['nodeSelector']['workload'], 'mysql-cluster')
        self.assertEqual(replica['spec']['serviceAccountName'], 'mycluster2-sidecar-sa')

    def test_operator_is_scoped_to_two_namespaces_and_workers(self):
        values = rt.operator_values()
        self.assertEqual(values['deployment']['namespaces'], ['mysql', 'mysql-secondary'])
        self.assertEqual(values['deployment']['nodeSelector']['workload'], 'mysql-cluster')

    def test_scc_is_fixed_uid_without_root_or_host_access(self):
        for uid in (2, 27, 999):
            scc = rt.scc(uid)
            self.assertEqual(scc['runAsUser'], {'type': 'MustRunAs', 'uid': uid})
            self.assertEqual(scc['fsGroup']['ranges'], [{'min': uid, 'max': uid}])
            self.assertFalse(scc['allowPrivilegedContainer'])
            self.assertFalse(scc['allowPrivilegeEscalation'])
            self.assertFalse(scc['allowHostNetwork'])
            self.assertNotIn('hostPath', scc['volumes'])
            self.assertEqual(scc['users'], [])
            self.assertEqual(scc['groups'], [])

    def test_scc_bindings_never_target_default_or_whole_namespace(self):
        docs = rt.security_documents()
        roles = [d for d in docs if d['kind'] == 'Role']
        self.assertTrue(all(d['rules'][0]['verbs'] == ['use'] for d in roles))
        bindings = [d for d in docs if d['kind'] == 'RoleBinding']
        for d in bindings:
            for subject in d['subjects']:
                self.assertEqual(subject['kind'], 'ServiceAccount')
                self.assertNotEqual(subject['name'], 'default')
                self.assertEqual(subject['namespace'], d['metadata']['namespace'])

    def test_control_plane_cannot_be_selected_even_with_worker_role(self):
        with self.assertRaisesRegex(ValueError, 'control-plane'):
            rt.select_workers({'items': [node('worker01', control=True)]}, ['worker01'])

    def test_label_script_resolves_fqdn_and_rejects_ambiguous_short_name(self):
        doc = {'items': [node('worker01.internal.test')]}
        self.assertEqual(rt.select_workers(doc, ['worker01'])[0]['metadata']['name'], 'worker01.internal.test')
        doc['items'].append(node('worker01.other.test'))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            rt.select_workers(doc, ['worker01'])

    def test_label_script_does_not_overwrite_another_workload(self):
        with self.assertRaisesRegex(ValueError, 'another workload'):
            rt.select_workers({'items': [node('worker01', workload='other')]}, ['worker01'])

    def test_workers_must_be_labelled_and_sufficient(self):
        doc = {'items': [node('worker'+str(i).zfill(2)) for i in range(1,4)]}
        self.assertEqual(len(rt.workers(doc)), 3)
        doc['items'][0]['metadata']['labels'].pop('workload')
        with self.assertRaisesRegex(clusterset.SetupError, 'step 02'):
            rt.workers(doc)

    def test_dedicated_namespaces_and_primary_settings(self):
        os.environ['REPLICA_MYSQL_NAMESPACE'] = 'mysql'
        with self.assertRaisesRegex(clusterset.SetupError, 'different namespace'):
            rt.validate()

    def test_wrong_api_stops_before_any_mutation(self):
        kube = self.path/'kubeconfig'; kube.write_text('test')
        os.environ.update(KUBECONFIG=str(kube), MYSQL_EXPECTED_API_SERVER='https://expected.test:6443')
        with patch.object(clusterset, 'kubectl', return_value={'clusters': [{'cluster': {'server': 'https://other.test:6443'}}]}) as command:
            with self.assertRaisesRegex(clusterset.SetupError, 'Wrong cluster'):
                rt.guard()
        self.assertEqual(command.call_count, 1)

    def test_label_validation_dry_runs_all_nodes_before_patching(self):
        import label_workers
        kube = self.path/'kubeconfig'; kube.write_text('test')
        os.environ.update(KUBECONFIG=str(kube), MYSQL_EXPECTED_API_SERVER='https://expected.test:6443', MYSQL_STATE_DIR=str(self.path/'state'))
        doc = {'items': [node('worker'+str(i).zfill(2), workload=None) for i in range(1,4)]}
        calls = []
        def fake(args):
            calls.append(args)
            if args[0]=='config':return {'clusters': [{'cluster': {'server': os.environ['MYSQL_EXPECTED_API_SERVER']}}]}
            if args[0]=='get':return doc
            return {}
        with patch.object(label_workers, 'command', side_effect=fake):
            label_workers.main()
        patches = [a for a in calls if a[0]=='patch']
        self.assertEqual(len(patches), 6)
        self.assertTrue(all('--dry-run=server' in a for a in patches[:3]))
        self.assertTrue(all('--dry-run=server' not in a for a in patches[3:]))
        self.assertTrue((self.path/'state/workers-before-label.json').exists())

    def test_oci_storage_is_dedicated_block_only_and_keeps_scc_admission(self):
        source = [
            {'kind': 'Deployment', 'metadata': {'name': 'csi-oci-controller'}, 'spec': {'template': {'spec': {'containers': [{'name': 'oci-csi-controller-driver'}]}}}},
            {'kind': 'Namespace', 'metadata': {'name': 'oci-csi', 'labels': {'openshift.io/run-level': '0'}}},
            {'kind': 'StorageClass', 'metadata': {'name': 'oci-bv'}},
            {'kind': 'CSIDriver', 'metadata': {'name': 'fss.csi.oraclecloud.com'}},
            {'kind': 'CSIDriver', 'metadata': {'name': 'blockvolume.csi.oraclecloud.com'}},
            {'kind': 'DaemonSet', 'metadata': {'name': 'csi-oci-node'}, 'spec': {'template': {'spec': {
                'containers': [
                    {'name': 'oci-csi-node-driver', 'args': ['--endpoint=unix:///csi/csi.sock', '--fss-endpoint=unix:///fss/csi.sock'],
                     'volumeMounts': [{'name': 'fss-plugin-dir'}, {'name': 'plugin-dir'}]},
                    {'name': 'csi-node-registrar-fss'}],
                'volumes': [{'name': 'fss-plugin-dir'}, {'name': 'plugin-dir'}]}}}}
        ]
        docs = storage.transform(source, {'compartment_id': 'test-compartment', 'vcn_id': 'test-vcn', 'region': 'test-region'})
        ns = next(d for d in docs if d['kind'] == 'Namespace')
        self.assertNotIn('openshift.io/run-level', ns['metadata']['labels'])
        classes = [d for d in docs if d['kind'] == 'StorageClass']
        self.assertEqual(len(classes), 1)
        self.assertEqual(classes[0]['metadata']['name'], 'mysql-oci-bv')
        self.assertEqual(classes[0]['reclaimPolicy'], 'Retain')
        self.assertEqual(classes[0]['volumeBindingMode'], 'WaitForFirstConsumer')
        self.assertNotIn('annotations', classes[0]['metadata'])
        pod = next(d for d in docs if d['kind'] == 'DaemonSet')['spec']['template']['spec']
        self.assertEqual(len(pod['containers']), 1)
        self.assertIn('--fss-csi-driver-enabled=false', pod['containers'][0]['args'])
        self.assertEqual(pod['nodeSelector']['workload'], 'mysql-cluster')
        self.assertEqual([v['name'] for v in pod['volumes']], ['plugin-dir'])
        binding = next(d for d in docs if d['kind'] == 'RoleBinding')
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'csi-oci-node-sa', 'namespace': 'oci-csi'}])
        self.assertTrue(next(d for d in docs if d['kind'] == 'Secret')['stringData']['config.yaml'].find('useInstancePrincipals') >= 0)
        self.assertEqual(source[1]['metadata']['labels']['openshift.io/run-level'], '0')
        controller = next(d for d in docs if d['kind'] == 'Deployment')
        self.assertEqual(controller['spec']['template']['spec']['dnsPolicy'], 'Default')
        self.assertEqual(controller['spec']['minReadySeconds'], 35)
        self.assertIn('readinessProbe', controller['spec']['template']['spec']['containers'][0])

    def test_oci_node_metadata_conflict_stops_before_any_patch(self):
        n = node('worker01')
        n['spec'] = {'providerID': 'another-provider'}
        inventory = {'compartment_id': 'test-compartment', 'nodes': [{'node': n,
                     'instance_id': 'test-instance', 'availability_domain': 'prefix:TEST-AD-1', 'region': 'test-region'}]}
        with patch.object(clusterset, 'get', return_value=n), patch.object(clusterset, 'kubectl') as command:
            with self.assertRaisesRegex(clusterset.SetupError, 'providerID conflicts'):
                storage.prepare_nodes(inventory)
            command.assert_not_called()

    def test_storage_authorization_denial_fails_fast_for_current_pending_claim(self):
        pvc = {'metadata': {'name': 'datadir-mycluster-0', 'uid': 'current-pvc'}, 'status': {'phase': 'Pending'}}
        events = {'items': [{'involvedObject': {'uid': 'old-pvc'}, 'message': 'NotAuthorizedOrNotFound'},
                            {'involvedObject': {'uid': 'current-pvc'}, 'message': 'CreateVolume NotAuthorizedOrNotFound'}]}
        def fake_get(kind, **kwargs): return {'items': [pvc]} if kind == 'pvc' else events
        with patch.object(clusterset, 'get', side_effect=fake_get):
            with self.assertRaisesRegex(clusterset.SetupError, 'storage authorization denied'):
                rt.storage_errors('mysql', 'mycluster')
            events['items'][1]['lastTimestamp'] = '2000-01-01T00:00:00Z'
            rt.storage_errors('mysql', 'mycluster')
            pvc['status']['phase'] = 'Bound'
            rt.storage_errors('mysql', 'mycluster')
            pvc['status']['phase'] = 'Pending'
            events['items'] = events['items'][:1]
            rt.storage_errors('mysql', 'mycluster')

    def test_volume_audit_rejects_other_compartment_and_control_plane_attachment(self):
        worker = node('worker01')
        control = node('control', control=True)
        inventory = {'nodes': [{'instance_id': 'worker-instance', 'node': worker},
                               {'instance_id': 'control-instance', 'node': control}]}
        volume = types.SimpleNamespace(id='volume-id', compartment_id='other-compartment', lifecycle_state='AVAILABLE', size_in_gbs=50)
        attachment = types.SimpleNamespace(lifecycle_state='ATTACHED', compartment_id='allowed-compartment', instance_id='control-instance')
        compute = Mock()
        sdk = types.SimpleNamespace(auth=types.SimpleNamespace(signers=types.SimpleNamespace(InstancePrincipalsSecurityTokenSigner=lambda: None)),
                                    core=types.SimpleNamespace(BlockstorageClient=lambda *args, **kwargs: types.SimpleNamespace(get_volume=lambda _: types.SimpleNamespace(data=volume))),
                                    retry=types.SimpleNamespace(NoneRetryStrategy=lambda: None),
                                    pagination=types.SimpleNamespace(list_call_get_all_results=lambda *args, **kwargs: types.SimpleNamespace(data=[attachment])))
        def get_resource(kind, name=None, namespace=None):
            if kind == 'pvc': return {'items': [{'metadata': {'name': 'datadir-mycluster-'+str(i), 'uid': 'uid'},
                                                 'status': {'phase': 'Bound'}, 'spec': {'volumeName': 'pv'}} for i in range(3)]}
            return {'metadata': {'name': 'pv'}, 'spec': {'claimRef': {'uid': 'uid'}, 'csi': {'driver': 'blockvolume.csi.oraclecloud.com', 'volumeHandle': 'volume-id'}}}
        with patch.object(storage, 'oci', sdk), patch.object(storage, 'scope', return_value=inventory), patch.object(rt, 'guard'), \
             patch.object(storage, 'clients', return_value=('allowed-compartment', 'allowed', 'region', compute, None)), \
             patch.object(clusterset, 'get', side_effect=get_resource):
            with self.assertRaisesRegex(clusterset.SetupError, 'compartment/state mismatch'):
                storage.audit()
            volume.compartment_id = 'allowed-compartment'
            with self.assertRaisesRegex(clusterset.SetupError, 'authorized worker'):
                storage.audit()
        compute.assert_not_called()

    def test_worker_device_config_never_applies_the_upstream_master_configuration(self):
        def config(role):
            return {'kind': 'MachineConfig', 'metadata': {'name': 'upstream-'+role, 'labels': {'machineconfiguration.openshift.io/role': role}},
                    'spec': {'config': {'storage': {'files': [{'path': '/etc/udev/rules.d/99-systemoci-persistent-names.rules'}, {'path': '/etc/ociudevpersistentnaming'}]}}}}
        source = [config('master'), config('worker')]
        doc = storage.worker_device_config(source)
        self.assertEqual(doc['metadata']['labels']['machineconfiguration.openshift.io/role'], 'worker')
        self.assertEqual(doc['metadata']['name'], '99-mysql-oci-consistent-device-path')
        self.assertEqual(source[0]['metadata']['name'], 'upstream-master')
        source[1]['spec']['config']['storage']['files'].append({'path': '/etc/another-file'})
        with self.assertRaisesRegex(clusterset.SetupError, 'Unexpected files'):
            storage.worker_device_config(source)

    def test_worker_machineconfig_refuses_additional_unverified_workers(self):
        selected = [node('worker'+str(i).zfill(2)) for i in range(1, 4)]
        inventory = {'nodes': [{'node': n} for n in selected]}
        nodes = {'items': selected+[node('control', control=True)]}
        def get(kind, name=None): return nodes if kind == 'nodes' else {'status': {'machineCount': 3}}
        with patch.object(clusterset, 'get', side_effect=get):
            self.assertEqual(storage.worker_pool_scope(inventory), 3)
            nodes['items'].append(node('other-worker'))
            with self.assertRaisesRegex(clusterset.SetupError, 'outside the verified worker set'):
                storage.worker_pool_scope(inventory)

    def test_overlay_nsg_preserves_existing_membership_and_uses_concurrency_guard(self):
        ns = types.SimpleNamespace
        n = node('worker01'); n['status']['addresses'] = [{'type': 'InternalIP', 'address': '192.0.2.10'}]
        inventory = {'compartment_id': 'allowed', 'vcn_id': 'vcn', 'nodes': [{'node': n, 'instance_id': 'instance'}]}
        vnic = ns(id='vnic', private_ip='192.0.2.10', compartment_id='allowed', subnet_id='subnet', nsg_ids=['existing-nsg'])
        network = Mock()
        network.get_vcn.return_value = ns(data=ns(compartment_id='allowed'))
        network.get_subnet.return_value = ns(data=ns(compartment_id='allowed', vcn_id='vcn'))
        network.get_vnic.return_value = ns(data=vnic, headers={'etag': 'current-etag'})
        group = ns(id='overlay-nsg', compartment_id='allowed', vcn_id='vcn', freeform_tags={'managed-by': storage.OWNER, 'cluster-uid': 'cluster-uid'})
        network.list_network_security_groups.return_value = ns(data=[group])
        network.get_network_security_group.return_value = ns(data=group)
        network.list_network_security_group_security_rules.return_value = ns(data=[])
        compute = Mock()
        compute.get_instance.return_value = ns(data=ns(compartment_id='allowed', lifecycle_state='RUNNING'))
        compute.list_vnic_attachments.return_value = ns(data=[ns(compartment_id='allowed', lifecycle_state='ATTACHED', vnic_id='vnic')])
        constructor = lambda **kwargs: ns(**kwargs)
        sdk = ns(pagination=ns(list_call_get_all_results=lambda function, *args, **kwargs: function(*args, **kwargs)),
                 core=ns(models=ns(AddSecurityRuleDetails=constructor, UdpOptions=constructor, PortRange=constructor,
                                   AddNetworkSecurityGroupSecurityRulesDetails=constructor, UpdateVnicDetails=constructor)))
        with patch.object(network_adapter, 'oci', sdk):
            network_adapter.prepare(inventory, compute, network, 'cluster-uid', self.path)
            args,kwargs = network.update_vnic.call_args
            self.assertEqual(args[1].nsg_ids, ['existing-nsg', 'overlay-nsg'])
            self.assertEqual(kwargs['if_match'], 'current-etag')
            rules = network.add_network_security_group_security_rules.call_args.args[1].security_rules
            self.assertEqual({r.direction for r in rules}, {'INGRESS','EGRESS'})
            self.assertTrue(all(r.protocol == '17' and r.udp_options.destination_port_range.min == 6081 for r in rules))
            network.create_network_security_group.assert_not_called()
            network.update_vnic.reset_mock()
            vnic.compartment_id = 'outside'
            with self.assertRaisesRegex(ValueError, 'authorized compartment'):
                network_adapter.prepare(inventory, compute, network, 'cluster-uid', self.path)
            network.update_vnic.assert_not_called()

    def test_pod_tcp_probe_rejects_cross_worker_failure_without_credentials(self):
        pods = [{'metadata': {'name': 'mycluster-'+str(i)}, 'status': {'containerStatuses': [{'ready': True}, {'ready': True}]}} for i in range(3)]
        calls = []
        def command(*args, **kwargs):
            calls.append((args, kwargs))
            if 'get' in args: return {'items': pods}
            return 'MYSQL_NETWORK_JSON='+json.dumps([{'target': 'mycluster-1', 'ok': False, 'error': 'TimeoutError'}])
        with patch.object(clusterset, 'kubectl', side_effect=command):
            with self.assertRaisesRegex(clusterset.SetupError, 'UDP 6081'):
                rt.network_check()
        self.assertTrue(all('payload' not in kwargs for args,kwargs in calls))
        self.assertEqual(len(calls), 4)

    def test_numbered_scripts_are_contiguous_in_execution_order(self):
        scripts = sorted(DIR.glob('[0-9][0-9]-*.sh'))
        self.assertEqual([int(s.name[:2]) for s in scripts], list(range(1, 13)))
        self.assertEqual(scripts[2].name, '03-prepare-oci-storage.sh')
        self.assertEqual(scripts[3].name, '04-validate-environment.sh')

    def test_capacity_rejects_defaults_on_small_workers_and_accepts_explicit_lab_tuning(self):
        nodes = {'items': [node('worker'+str(i).zfill(2)) for i in range(1, 4)]}
        for n in nodes['items']: n['status']['allocatable'] = {'cpu': '1500m'}
        pods = {'items': [{'metadata': {'namespace': 'openshift-test'},
                           'spec': {'nodeName': n['metadata']['name'], 'containers': [{'resources': {'requests': {'cpu': '374m'}}}]}}
                          for n in nodes['items']]}
        with self.assertRaisesRegex(clusterset.SetupError, 'need at least'):
            rt.capacity(nodes, pods)
        os.environ['MYSQL_SERVER_CPU_REQUEST'] = '250m'
        rt.capacity(nodes, pods)

    def test_help_does_not_need_cluster_or_private_config(self):
        for script in DIR.glob('[0-9][0-9]-*.sh'):
            result = subprocess.run(['bash', str(script), '--help'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, script.name+result.stderr)

    def test_replica_inspection_does_not_follow_router_to_global_primary(self):
        kube = self.path/'kubeconfig'; kube.write_text('test')
        shared = self.path/'okd.env'
        shared.write_text('ADMIN_WORKDIR='+str(self.path)+'\n')
        trace = self.path/'commands.jsonl'
        oc = self.path/'oc'
        oc.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                      'a=sys.argv[1:]\n'
                      'with open(os.environ["OC_TEST_TRACE"],"a") as f:f.write(json.dumps(a)+"\\n")\n'
                      'if "current-context" in a:print("test-context")\n'
                      'elif "view" in a:print(os.environ["MYSQL_EXPECTED_API_SERVER"])\n')
        oc.chmod(0o700)
        kubectl = self.path/'kubectl'; kubectl.write_text('#!/bin/sh\nexit 0\n'); kubectl.chmod(0o700)
        env = {**os.environ, 'PATH': str(self.path)+os.pathsep+os.environ['PATH'],
               'KUBECONFIG': str(kube), 'OKD_CONFIG_FILE': str(shared),
               'MYSQL_EXPECTED_API_SERVER': 'https://expected.test:6443',
               'MYSQL_OPENSHIFT_CONFIG': str(self.cfg), 'OC_TEST_TRACE': str(trace)}
        # Leave API selection to the test fixture rather than the blank example field.
        self.cfg.write_text(self.cfg.read_text().replace('MYSQL_EXPECTED_API_SERVER=', 'MYSQL_EXPECTED_API_SERVER=https://expected.test:6443'))
        subprocess.run(['bash', str(DIR/'11-connect-mysql.sh'), '--replica'], env=env, check=True, capture_output=True, text=True)
        subprocess.run(['bash', str(DIR/'12-port-forward-mysql.sh'), '--replica'], env=env, check=True, capture_output=True, text=True)
        calls = [json.loads(x) for x in trace.read_text().splitlines()]
        connection = next(x for x in calls if 'mysqlsh' in x)
        self.assertIn('mysql-secondary', connection)
        self.assertEqual(connection[connection.index('--host')+1], '127.0.0.1')
        forwarding = next(x for x in calls if 'port-forward' in x)
        self.assertIn('service/mycluster2-instances', forwarding)
        self.assertIn('3306:3306', forwarding)


if __name__ == '__main__':
    unittest.main()
