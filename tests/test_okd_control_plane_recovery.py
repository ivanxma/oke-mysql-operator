"""Recover only a current single-node controller-manager API-timeout failure."""
import importlib.util
import contextlib
import io
import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('bootstrap_diagnosis',Path(__file__).resolve().parents[1]/'openshift/community/lib/diagnose-control-plane.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RecoveryPlanTests(unittest.TestCase):
    def setUp(self):
        self.operator={'spec':{'managementState':'Managed'},'status':{
            'latestAvailableRevision':2,'nodeStatuses':[{'nodeName':'node.test','currentRevision':0}]}}
        self.pods=[{'metadata':{'name':'installer-2-node.test'},'spec':{'nodeName':'node.test'},
                    'status':{'phase':'Failed','containerStatuses':[{'state':{'terminated':{'message':'Client.Timeout exceeded'}}}]}}]

    def test_confirmed_timeout_failure_can_be_retried(self):
        self.assertEqual(module.recovery_plan(self.operator,self.pods,'node.test')['revision'],2)

    def test_current_revision_is_noop(self):
        self.operator['status']['nodeStatuses'][0]['currentRevision']=2
        self.assertIsNone(module.recovery_plan(self.operator,self.pods,'node.test'))

    def test_historical_failure_does_not_allow_recovery(self):
        self.pods[0]['metadata']['name']='installer-1-node.test'
        with self.assertRaisesRegex(ValueError,'not confirmed failed'):
            module.recovery_plan(self.operator,self.pods,'node.test')

    def test_pending_or_completed_installer_does_not_allow_recovery(self):
        for phase in ('Running','Pending','Succeeded'):
            self.pods[0]['status']['phase']=phase
            with self.assertRaisesRegex(ValueError,'not confirmed failed'):
                module.recovery_plan(self.operator,self.pods,'node.test')

    def test_wrong_node_is_refused(self):
        with self.assertRaisesRegex(ValueError,'selected single-node'):
            module.recovery_plan(self.operator,self.pods,'another.test')

    def test_other_installer_failure_is_refused(self):
        self.pods[0]['status']['containerStatuses']=[]
        with self.assertRaisesRegex(ValueError,'not a confirmed API timeout'):
            module.recovery_plan(self.operator,self.pods,'node.test')

    def test_repeated_rollout_is_refused(self):
        self.operator['spec']['forceRedeploymentReason']='okd-bootstrap-recovery-earlier'
        with self.assertRaisesRegex(ValueError,'already requested'):
            module.recovery_plan(self.operator,self.pods,'node.test')

    def test_unmanaged_operator_is_refused(self):
        self.operator['spec']['managementState']='Unmanaged'
        with self.assertRaisesRegex(ValueError,'Managed'):
            module.recovery_plan(self.operator,self.pods,'node.test')


class RecoveryExecutionTests(unittest.TestCase):
    def setUp(self):
        RecoveryPlanTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'sno/auth').mkdir(parents=True)
        self.config=self.root/'sno/auth/kubeconfig'
        self.config.write_text('synthetic config')
        (self.root/'cluster-domain').write_text('cluster.invalid')
        self.operator['metadata']={'name':'cluster','resourceVersion':'42'}
        def good(name):
            return {'metadata':{'name':name},'status':{'conditions':[
                {'type':'Available','status':'True'},{'type':'Progressing','status':'False'},
                {'type':'Degraded','status':'False'}]}}
        self.operators=[good('kube-apiserver'),good('etcd')]
        self.nodes=[{'metadata':{'name':'node.test'},'status':{
            'addresses':[{'type':'InternalIP','address':'192.0.2.20'}],
            'conditions':[{'type':'Ready','status':'True'}]}}]
        self.calls=[]

    def command(self,args,**kwargs):
        self.calls.append(args)
        if 'config' in args:
            output='https://api.cluster.invalid:6443'
        elif 'patch' in args:
            output='patched'
        else:
            path=next(arg.split('=',1)[1] for arg in args if arg.startswith('--raw='))
            values={'/readyz?verbose':'[+]ping ok\nreadyz check passed',
                '/api/v1/nodes':{'items':self.nodes},
                '/apis/config.openshift.io/v1/clusteroperators':{'items':self.operators},
                '/apis/apiregistration.k8s.io/v1/apiservices':{'items':[]},
                '/apis/operator.openshift.io/v1/kubecontrollermanagers/cluster':self.operator,
                '/api/v1/namespaces/openshift-kube-controller-manager/pods':{'items':self.pods}}
            value=values[path]
            output=value if isinstance(value,str) else json.dumps(value)
        return subprocess.CompletedProcess(args,0,output,'')

    def execute(self,mode):
        with patch.object(module.subprocess,'run',side_effect=self.command),contextlib.redirect_stdout(io.StringIO()):
            return module.run(str(self.root),str(self.config),'cluster.invalid','192.0.2.20',mode)

    def test_diagnose_never_patches(self):
        self.assertEqual(self.execute('diagnose'),0)
        self.assertFalse(any('patch' in call for call in self.calls))
        report=next((self.root/'diagnostics').glob('*.json'))
        self.assertEqual(report.stat().st_mode & 0o777,0o600)

    def test_preview_never_patches(self):
        self.assertEqual(self.execute('preview'),0)
        self.assertFalse(any('patch' in call for call in self.calls))

    def test_apply_patches_only_controller_manager_with_version_guard(self):
        self.assertEqual(self.execute('apply'),0)
        mutations=[call for call in self.calls if 'patch' in call]
        self.assertEqual(len(mutations),1)
        self.assertIn('kubecontrollermanagers.operator.openshift.io',mutations[0])
        changes=json.loads(mutations[0][-1])
        self.assertEqual(changes[0],{'op':'test','path':'/metadata/resourceVersion','value':'42'})
        self.assertEqual(changes[1]['path'],'/spec/forceRedeploymentReason')

    def test_unhealthy_etcd_blocks_mutation(self):
        self.operators[1]['status']['conditions'][0]['status']='False'
        with self.assertRaisesRegex(ValueError,'etcd must be Available'):
            self.execute('apply')
        self.assertFalse(any('patch' in call for call in self.calls))

    def test_wrong_node_address_blocks_mutation(self):
        self.nodes[0]['status']['addresses'][0]['address']='192.0.2.99'
        with self.assertRaisesRegex(ValueError,'registered private address'):
            self.execute('apply')
        self.assertFalse(any('patch' in call for call in self.calls))

    def test_wrong_kubeconfig_blocks_all_commands(self):
        other=self.root/'wrong-config';other.write_text('synthetic')
        with patch.object(module.subprocess,'run',side_effect=self.command),self.assertRaisesRegex(ValueError,'installation auth'):
            module.run(str(self.root),str(other),'cluster.invalid','192.0.2.20','apply')
        self.assertEqual(self.calls,[])


if __name__=='__main__':
    unittest.main()
