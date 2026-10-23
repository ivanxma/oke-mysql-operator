"""Submariner London-first scope, identity and command guards; no live clusters."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/'openshift/community/networking/submariner'
spec=importlib.util.spec_from_file_location('submariner_workflow',DIR/'lib/workflow.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class SubmarinerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.kube=self.root/'kubeconfig';self.kube.write_text('synthetic');self.kube.chmod(0o600)
        self.peer=self.root/'peerconfig';self.peer.write_text('synthetic');self.peer.chmod(0o600)
        self.env=dict(ADMIN_WORKDIR=str(self.root),SUBMARINER_STATE_DIR=str(self.root/'internal_doc/submariner'),
                      SUBMARINER_VERSION='0.24.2',KUBECONFIG=str(self.kube),
                      SUBMARINER_TARGET_API='https://api.target.test:6443',
                      SUBMARINER_TARGET_CONTEXT='same-context',SUBMARINER_TARGET_ID='target',
                      SUBMARINER_TARGET_GATEWAY='worker-target',OCI_REGION='uk-london-1',
                      MACHINE_CIDR='192.0.2.0/24',SUBMARINER_PEER_MACHINE_CIDR='198.51.100.0/24',
                      SUBMARINER_ENABLE_PEER='false',SUBMARINER_PEER_INSTALLATION_VERIFIED='false',
                      SUBMARINER_PEER_CONTEXT='same-context',SUBMARINER_PEER_KUBECONFIG=str(self.peer),
                      SUBMARINER_PEER_API='https://api.peer.test:6443',SUBMARINER_PEER_ID='peer',
                      SUBMARINER_LONDON_INSTALLATION_VERIFIED='true',SUBMARINER_COMPATIBILITY_REVIEWED='true',
                      SUBMARINER_NETWORK_REVIEWED='true')
        self.w=module.Workflow(self.env)
        self.target=dict(uid='target-uid',id='target',cidrs=['10.132.0.0/14','172.31.0.0/16','192.0.2.0/24'],
                         gateway='worker-target',node_uid='node-uid',node_resource_version='12')
        self.other=dict(uid='peer-uid',id='peer',cidrs=['10.128.0.0/14','172.30.0.0/16','198.51.100.0/24'],gateway='worker-peer')

    def enable_peer(self):
        self.w.env['SUBMARINER_ENABLE_PEER']='true';self.w.env['SUBMARINER_PEER_INSTALLATION_VERIFIED']='true'
        self.w.env['SUBMARINER_PEER_NETWORK_REVIEWED']='true'

    def test_generated_peer_registration_discovers_fields_without_cluster_writes(self):
        repo=self.root.resolve()/'repo';selected=repo/'generated/openshift/uk-london-1/jumphost/community.env'
        selected.parent.mkdir(parents=True);selected.write_text('');selected.chmod(0o600)
        peer=repo/'generated/openshift/eu-frankfurt-1/jumphost/community.env'
        peer.parent.mkdir(parents=True);peer.write_text('');peer.chmod(0o600)
        config=selected.with_name('submariner.env');config.write_text('SUBMARINER_ENABLE_PEER=false\n');config.chmod(0o600)
        optional=selected.with_name('submariner.optional.env');optional.write_text((DIR/'config.optional.env.example').read_text());optional.chmod(0o600)
        self.w.env.update(OKD_CONFIG_FILE=str(selected),SUBMARINER_CONFIG_FILE=str(config),OCI_COMPARTMENT_ID='cid')
        self.w.validation=Mock(return_value={'target':self.target,'peer':self.other});self.w.sub=Mock()
        self.w.selection=Mock(return_value=(['oc'],self.peer,'same-context'))
        nodes={'items':[{'metadata':{'name':'worker-peer','labels':{'node-role.kubernetes.io/worker':''}}}]}
        pods={'items':[{'metadata':{'name':'db'},'status':{'phase':'Running'},'spec':{'containers':[{'name':'nbdb'}]}}]}
        self.w.read=Mock(side_effect=[nodes,pods])
        values=dict(KUBECONFIG=str(self.peer),MACHINE_CIDR='198.51.100.0/24',CLUSTER_NAME='peer',BASE_DOMAIN='test',OCI_COMPARTMENT_ID='cid',OCI_REGION='eu-frankfurt-1')
        with patch.object(module,'__file__',str(repo/'openshift/community/networking/submariner/lib/workflow.py')),patch.object(module,'run',side_effect=[json.dumps(values),'7.18.0']):
            self.w.register_peer('eu-frankfurt-1')
        self.assertIn('SUBMARINER_ENABLE_PEER=true',config.read_text())
        self.assertIn('SUBMARINER_PEER_GATEWAY=worker-peer',optional.read_text())
        self.assertIn('SUBMARINER_PEER_NETWORK_REVIEWED=false',optional.read_text())
        self.w.sub.assert_called_once_with(['diagnose','k8s-version'],peer=True)
        self.assertEqual(json.loads((self.w.state/'peer-registration.json').read_text())['uid'],'peer-uid')

    def test_peer_mutations_require_separate_network_review_before_health_or_writes(self):
        self.enable_peer();self.w.env['SUBMARINER_PEER_NETWORK_REVIEWED']='false'
        self.w.health=Mock();self.w.sub=Mock()
        with self.assertRaisesRegex(ValueError,'peer gateway'):self.w.execute('peer')
        self.w.health.assert_not_called();self.w.sub.assert_not_called()

    def test_local_review_checks_schema_before_updating_private_flags(self):
        config=self.root/'submariner.env'
        keys=('SUBMARINER_LONDON_INSTALLATION_VERIFIED','SUBMARINER_COMPATIBILITY_REVIEWED','SUBMARINER_NETWORK_REVIEWED')
        config.write_text(''.join(k+'=false\n' for k in keys));config.chmod(0o600)
        self.w.env['SUBMARINER_CONFIG_FILE']=str(config);self.w.sub=Mock();self.w.validation=Mock(return_value={'target':self.target})
        self.w.selection=Mock(return_value=(['oc'],self.kube,'context'))
        self.w.read=Mock(return_value={'items':[{'metadata':{'name':'ovn-test'},'spec':{'containers':[{'name':'nbdb'}]},'status':{'phase':'Running'}}]})
        with patch.object(module,'run',return_value='6.0.0'),self.assertRaisesRegex(ValueError,'schema'):self.w.review_local()
        self.assertIn('=false',config.read_text());self.w.validation.assert_not_called()
        with patch.object(module,'run',return_value='7.18.0'):self.w.review_local()
        self.assertNotIn('=false',config.read_text())
        self.assertEqual(json.loads((self.w.state/'prerequisite-review.json').read_text())['cluster_uid'],'target-uid')

    def test_broker_created_operator_can_be_used_for_first_local_join_only(self):
        ns={'metadata':{'uid':'operator-namespace'}}
        deployment={'spec':{'template':{'spec':{'containers':[{'image':'quay.io/submariner/submariner-operator:0.24.2'}]}}}}
        owner=module.broker_operator_reusable({'uid':'target-uid'},'target-uid',ns,deployment,{'items':[]},'0.24.2')
        self.assertEqual(owner['operator_namespace_uid'],'operator-namespace')
        for extra in [dict(peer=True)]:
            with self.assertRaises(ValueError):module.broker_operator_reusable(owner,'target-uid',ns,deployment,{'items':[]},'0.24.2',**extra)
        with self.assertRaises(ValueError):module.broker_operator_reusable(owner,'target-uid',ns,deployment,{'items':[{}]},'0.24.2')
        with self.assertRaises(ValueError):module.broker_operator_reusable(owner,'other-uid',ns,deployment,{'items':[]},'0.24.2')
        with self.assertRaises(ValueError):module.broker_operator_reusable(owner,'target-uid',{'metadata':{'uid':'changed'}},deployment,{'items':[]},'0.24.2')

    def test_download_checksum_mismatch_refused_before_binary_execution(self):
        import io
        import types
        asset='subctl-v0.24.2-linux-amd64.tar.gz'
        responses=[io.BytesIO(('0'*64+'  '+asset+'\n').encode()),io.BytesIO(b'tampered')]
        with patch.object(module.sys,'platform','linux'),patch.object(module.os,'uname',return_value=types.SimpleNamespace(machine='x86_64')),patch.object(module.urllib.request,'urlopen',side_effect=responses),patch.object(module,'run') as call:
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):self.w.download()
            call.assert_not_called()
        self.assertFalse(self.w.bin.exists())

    def test_download_extracts_only_verified_binary(self):
        import io
        import types
        import tarfile
        import hashlib
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for name,data in [('subctl',b'synthetic-client'),('../../outside',b'bad')]:
                entry=tarfile.TarInfo(name);entry.size=len(data);archive.addfile(entry,io.BytesIO(data))
        data=stream.getvalue();asset='subctl-v0.24.2-linux-amd64.tar.gz'
        responses=[io.BytesIO((hashlib.sha256(data).hexdigest()+'  '+asset+'\n').encode()),io.BytesIO(data)]
        with patch.object(module.sys,'platform','linux'),patch.object(module.os,'uname',return_value=types.SimpleNamespace(machine='x86_64')),patch.object(module.urllib.request,'urlopen',side_effect=responses),patch.object(module,'run',return_value='subctl version 0.24.2'):
            self.w.download()
        self.assertEqual(self.w.bin.read_bytes(),b'synthetic-client')
        self.assertEqual(self.w.bin.stat().st_mode & 0o777,0o700)
        self.assertFalse((self.root/'outside').exists())

    def test_syntax_and_help_never_contact_cluster(self):
        for path in DIR.rglob('*.sh'):subprocess.run(['bash','-n',str(path)],check=True)
        for path in DIR.glob('[0-9][0-9]-*.sh'):
            r=subprocess.run(['bash',str(path),'--help'],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_london_only_validation_never_reads_peer(self):
        self.w.health=Mock(return_value=self.target)
        self.assertEqual(self.w.validation(),{'target':self.target})
        self.w.health.assert_called_once_with()
        self.assertEqual((self.w.state/'clusters.json').stat().st_mode & 0o777,0o600)

    def test_peer_join_refused_before_any_labels_or_subctl(self):
        self.w.health=Mock(return_value=self.target);self.w.label=Mock();self.w.sub=Mock()
        with self.assertRaisesRegex(ValueError,'Peer phase disabled'):self.w.execute('peer')
        self.w.label.assert_not_called();self.w.sub.assert_not_called()

    def test_same_cluster_selected_twice_refused(self):
        self.enable_peer();self.w.health=Mock(side_effect=[self.target,{**self.other,'uid':'target-uid'}])
        with self.assertRaisesRegex(ValueError,'same Kubernetes cluster'):self.w.validation()

    def test_peer_cidrs_overlap_refused(self):
        self.enable_peer();self.w.health=Mock(side_effect=[self.target,{**self.other,'cidrs':['10.132.0.0/14']}])
        with self.assertRaisesRegex(ValueError,'overlap'):self.w.validation()

    def test_machine_service_overlap_across_clusters_refused(self):
        with self.assertRaises(ValueError):module.ensure_disjoint(['192.0.2.0/24'],['192.0.2.0/25'])

    def test_saved_peer_identity_survives_london_only_validation(self):
        module.private_write(self.w.state/'clusters.json',{'target':self.target,'peer':self.other})
        self.w.health=Mock(return_value=self.target);self.w.validation()
        self.assertEqual(json.loads((self.w.state/'clusters.json').read_text())['peer']['uid'],'peer-uid')

    def test_replaced_cluster_uid_refused(self):
        module.private_write(self.w.state/'clusters.json',{'target':self.target})
        self.w.health=Mock(return_value={**self.target,'uid':'replacement'})
        with self.assertRaisesRegex(ValueError,'identity differs'):self.w.validation()

    def test_wrong_expected_api_rejected_before_live_read(self):
        with patch.object(module,'run',return_value='https://wrong.test:6443'):
            with self.assertRaisesRegex(ValueError,'API'):self.w.selection()

    def test_default_kubeconfig_rejected_without_oc(self):
        self.w.env['KUBECONFIG']=str(Path.home()/'.kube/config')
        with patch.object(module,'run') as run:
            with self.assertRaises(ValueError):self.w.selection()
            run.assert_not_called()

    def test_two_equal_context_names_use_separate_config_flags(self):
        self.w.bin.parent.mkdir(parents=True);self.w.bin.write_text('fake')
        self.w.selection=Mock(side_effect=[([],self.kube,'same-context'),([],self.peer,'same-context')])
        with patch.object(module,'run',return_value='0.24.2'),patch.object(module.subprocess,'run') as call:
            self.w.sub(['verify','--disruptive-tests=false'],both=True)
        args=call.call_args.args[0]
        self.assertEqual(args[args.index('--kubeconfig')+1],str(self.kube))
        self.assertEqual(args[args.index('--toconfig')+1],str(self.peer))
        self.assertIn('--disruptive-tests=false',args)
        self.assertEqual(call.call_args.kwargs['stdin'],subprocess.DEVNULL)

    def test_non_london_initial_target_refused_before_oc(self):
        self.w.env['OCI_REGION']='eu-frankfurt-1'
        with patch.object(module,'run') as call:
            with self.assertRaisesRegex(ValueError,'London'):self.w.health()
            call.assert_not_called()

    def test_installation_gate_refused_before_oc(self):
        self.w.env['SUBMARINER_LONDON_INSTALLATION_VERIFIED']='false'
        with patch.object(module,'run') as call:
            with self.assertRaisesRegex(ValueError,'Complete London'):self.w.health()
            call.assert_not_called()

    def test_missing_worker_and_control_plane_gateway_refused(self):
        def doc(peer,*args):
            if args==('get','clusteroperators'):
                return {'items':[{'status':{'conditions':[{'type':'Available','status':'True'},{'type':'Progressing','status':'False'},{'type':'Degraded','status':'False'}]}}]}
            if args==('get','machineconfigpools'):
                return {'items':[{'status':{'conditions':[{'type':'Updated','status':'True'},{'type':'Updating','status':'False'},{'type':'Degraded','status':'False'}]}}]}
            if args==('get','network.config.openshift.io','cluster'):
                return {'spec':{'networkType':'OVNKubernetes'},'status':{'networkType':'OVNKubernetes'}}
            return {'items':[{'metadata':{'name':'worker-target','labels':{'node-role.kubernetes.io/master':''}},'spec':{},
                              'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
        self.w.read=Mock(side_effect=doc);self.w.selection=Mock(return_value=(['oc'],self.kube,'context'))
        with patch.object(module,'run',return_value='ok'):
            with self.assertRaisesRegex(ValueError,'control-plane'):self.w.health()

    def test_gateway_patch_contains_uid_and_version_guards(self):
        self.w.health=Mock(return_value=self.target)
        self.w.selection=Mock(return_value=(['oc'],self.kube,'context'))
        self.w.read=Mock(return_value={'metadata':{'uid':'node-uid'}})
        with patch.object(module,'run') as call:self.w.label()
        args=call.call_args.args[0];patches=json.loads(args[args.index('-p')+1])
        self.assertEqual(patches[0],{'op':'test','path':'/metadata/uid','value':'node-uid'})
        self.assertEqual(patches[1]['path'],'/metadata/resourceVersion')
        self.assertEqual(patches[2]['path'],'/metadata/labels/submariner.io~1gateway')

    def initialization(self, worker=True):
        profile=self.root/'generated/openshift/uk-london-1/jumphost';profile.mkdir(parents=True,exist_ok=True)
        self.w.env.update(OKD_CONFIG_FILE=str(profile/'community.env'),SUBMARINER_CONFIG_FILE=str(profile/'submariner.env'),CLUSTER_NAME='okd-test')
        def resource(peer,*args):
            if args==('get','namespace','kube-system'):return {'metadata':{'uid':'target-uid'}}
            if args==('get','network.config.openshift.io','cluster'):return {'spec':{'networkType':'OVNKubernetes','clusterNetwork':[{'cidr':'10.132.0.0/14'}],'serviceNetwork':['172.31.0.0/16']}}
            if args==('get','clusteroperators'):return {'items':[{'status':{'conditions':[{'type':'Available','status':'True'},{'type':'Progressing','status':'False'},{'type':'Degraded','status':'False'}]}}]}
            if args==('get','machineconfigpools'):return {'items':[{'status':{'conditions':[{'type':'Updated','status':'True'},{'type':'Updating','status':'False'},{'type':'Degraded','status':'False'}]}}]}
            labels={'kubernetes.io/os':'linux','node-role.kubernetes.io/worker':''}
            if not worker:labels['node-role.kubernetes.io/master']=''
            return {'items':[{'metadata':{'name':'selected-worker','labels':labels},'spec':{},'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
        self.w.read=Mock(side_effect=resource);self.w.selection=Mock(return_value=(['oc'],self.kube,'context'))
        return profile

    def test_initialization_discovers_worker_without_cluster_changes(self):
        profile=self.initialization()
        self.w.sub=Mock();self.w.label=Mock()
        with patch.object(module,'run',return_value='ok'):self.w.initialize()
        text=(profile/'submariner.env').read_text()
        self.assertIn('SUBMARINER_TARGET_GATEWAY=selected-worker',text)
        self.assertIn('SUBMARINER_COMPATIBILITY_REVIEWED=false',text)
        self.assertIn('SUBMARINER_ENABLE_PEER=false',text)
        self.assertTrue((profile/'networking/submariner/01-install-subctl.sh').exists())
        self.assertEqual((profile/'submariner.env').stat().st_mode & 0o777,0o600)
        self.w.sub.assert_not_called();self.w.label.assert_not_called()

    def test_initialization_does_not_auto_select_control_plane(self):
        profile=self.initialization(worker=False)
        with patch.object(module,'run',return_value='ok'):self.w.initialize()
        self.assertIn("SUBMARINER_TARGET_GATEWAY=''",(profile/'submariner.env').read_text())
        self.initialization(worker=True)
        with patch.object(module,'run',return_value='ok'):self.w.initialize()
        self.assertIn('SUBMARINER_TARGET_GATEWAY=selected-worker',(profile/'submariner.env').read_text())

    def test_initialization_preserves_manual_settings_and_rejects_another_cluster(self):
        profile=self.initialization()
        with patch.object(module,'run',return_value='ok'):self.w.initialize()
        original='SUBMARINER_TARGET_ID=custom-id\nSUBMARINER_TARGET_GATEWAY=explicit-worker\n'
        (profile/'submariner.env').write_text(original)
        with patch.object(module,'run',return_value='ok'):self.w.initialize()
        self.assertEqual((profile/'submariner.env').read_text(),original)
        module.private_write(self.w.state/'discovery.json',{'uid':'different-cluster'})
        with patch.object(module,'run',return_value='ok'),self.assertRaisesRegex(ValueError,'another cluster'):self.w.initialize()

    def test_new_runtime_state_is_bound_to_generated_profile(self):
        env=dict(self.env,OKD_STATE_DIR=str(self.root/'generated/profile/.state'),SUBMARINER_STATE_DIR=str(self.root/'generated/profile/.state/networking/submariner'))
        self.assertEqual(module.Workflow(env).state,Path(env['SUBMARINER_STATE_DIR']))
        env['SUBMARINER_STATE_DIR']=str(self.root/'unrelated')
        with self.assertRaises(ValueError):module.Workflow(env)

    def test_diagnostics_do_not_require_healthy_operators(self):
        module.private_write(self.w.state/'clusters.json',{'target':self.target})
        self.w.health=Mock(side_effect=ValueError('unhealthy'))
        self.w.read=Mock(return_value={'metadata':{'uid':'target-uid'}});self.w.sub=Mock()
        with patch.object(module,'local_deployment_ready',return_value=True):self.w.execute('status')
        self.w.health.assert_not_called()
        self.assertEqual(self.w.sub.call_args_list[-1].args[0],['show','versions'])

    def test_single_cluster_status_requires_ready_components_and_no_remote_endpoint(self):
        deps={'items':[{'spec':{'replicas':1},'status':{'readyReplicas':1,'updatedReplicas':1}}]}
        sets={'items':[{'status':{'desiredNumberScheduled':1,'numberReady':1,'updatedNumberScheduled':1}}]}
        gateways={'items':[{'metadata':{'name':'worker-target'},'status':{'haStatus':'active','connections':[]}}]}
        endpoints={'items':[{'spec':{'cluster_id':'target'}}]}
        self.assertTrue(module.local_deployment_ready(deps,sets,gateways,endpoints,'target','worker-target'))
        endpoints['items'].append({'spec':{'cluster_id':'peer'}})
        self.assertFalse(module.local_deployment_ready(deps,sets,gateways,endpoints,'target','worker-target'))
        endpoints['items'].pop();sets['items'][0]['status']['numberReady']=0
        self.assertFalse(module.local_deployment_ready(deps,sets,gateways,endpoints,'target','worker-target'))


if __name__=='__main__':unittest.main()
