"""Read-only installation snapshots and secret redaction; no cluster access."""
import importlib.util
import json
import contextlib
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('install_monitor',Path(__file__).resolve().parents[1]/'openshift/community/lib/monitor-control-plane.py')
monitor=importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

def conditions(**values):
    return {'status':{'conditions':[{'type':k,'status':v} for k,v in values.items()]}}

class MonitorTests(unittest.TestCase):
    def test_password_redacted_but_progress_preserved(self):
        self.assertNotIn('secret',monitor.redact('level=info password: "secret"'))
        self.assertEqual(monitor.redact('Waiting for API...\n'),'Waiting for API...')

    def test_empty_cluster_is_not_ready(self):
        ready,lines=monitor.summarize([],[])
        self.assertFalse(ready)
        self.assertIn('Waiting for ClusterOperator objects',lines[-1])

    def test_settled_cluster(self):
        ready,lines=monitor.summarize([conditions(Ready='True')],[conditions(Available='True',Progressing='False',Degraded='False')])
        self.assertTrue(ready)
        self.assertIn('Nodes Ready: 1/1',lines)

    def test_progressing_operator_named(self):
        operator=conditions(Available='True',Progressing='True',Degraded='False')
        operator['metadata']={'name':'console'}
        ready,lines=monitor.summarize([conditions(Ready='True')],[operator])
        self.assertFalse(ready)
        self.assertIn('console',lines[-1])

    def test_wait_reports_specific_static_pod_condition(self):
        operator={'metadata':{'name':'kube-apiserver'},'status':{'conditions':[
            {'type':'Available','status':'False','reason':'StaticPods_ZeroNodesActive','message':'Node is at revision 0; new revision 3 pending'}]}}
        ready,lines=monitor.summarize([conditions(Ready='True')],[operator])
        self.assertFalse(ready)
        self.assertIn('revision 3',lines[-1])
        self.assertIn('StaticPods_ZeroNodesActive',lines[-1])

    def test_missing_dns_stops_api_probe(self):
        with patch.object(monitor,'execute',return_value=(2,'','')),patch.object(monitor,'emit') as emit:
            self.assertFalse(monitor.snapshot('oc',{},'cluster.invalid','192.0.2.20'))
        self.assertTrue(any('DNS does not resolve' in c.args[0] for c in emit.call_args_list))

    def test_api_not_ready_explains_wait(self):
        with patch.object(monitor,'execute',side_effect=[(0,'192.0.2.20 STREAM',''),(1,'','Connection refused')]),patch.object(monitor,'emit') as emit:
            self.assertFalse(monitor.snapshot('oc',{},'cluster.invalid','192.0.2.20'))
        self.assertTrue(any('Connection refused' in c.args[0] for c in emit.call_args_list))

    def test_snapshot_only_uses_read_commands(self):
        data=[(0,'192.0.2.20 STREAM',''),(0,'ok',''),
              (0,json.dumps({'items':[conditions(Ready='True')]}),''),
              (0,json.dumps({'items':[conditions(Available='True',Progressing='False',Degraded='False')]}),'')]
        with patch.object(monitor,'execute',side_effect=data) as execute,patch.object(monitor,'emit'):
            self.assertTrue(monitor.snapshot('oc',{},'cluster.invalid','192.0.2.20'))
        self.assertTrue(all(call.args[0][0]=='getent' or 'get' in call.args[0] for call in execute.call_args_list))

class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'sno/auth').mkdir(parents=True)
        (self.root/'sno/auth/kubeconfig').write_text('synthetic auth')
        (self.root/'cluster-domain').write_text('cluster.invalid')
        (self.root/'bin').mkdir()
        self.oc=self.root/'bin/oc'
        self.oc.write_text('#!/bin/sh\nprintf "https://api.cluster.invalid:6443"\n')
        self.oc.chmod(0o700)
        self.installer=self.root/'bin/openshift-install'
        self.installer.write_text('#!/bin/sh\necho "Waiting for operators"\necho password: synthetic-secret\nexit 0\n')
        self.installer.chmod(0o700)

    def run_monitor(self, mode='wait'):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),patch.object(monitor,'snapshot',return_value=True):
            result=monitor.monitor(str(self.root),str(self.root/'sno/auth/kubeconfig'),'cluster.invalid','192.0.2.20','5','10',mode)
        return result,output.getvalue()

    def test_streams_progress_and_redacts_password(self):
        code,output=self.run_monitor()
        self.assertEqual(code,0)
        self.assertIn('Waiting for operators',output)
        self.assertNotIn('synthetic-secret',output)
        self.assertIn('install-complete',output)

    def test_preserves_installer_failure(self):
        self.installer.write_text('#!/bin/sh\necho failed\nexit 7\n')
        code,output=self.run_monitor()
        self.assertEqual(code,7)
        self.assertIn('WAIT FAILED (exit 7)',output)

    def test_status_only_never_starts_installer(self):
        with patch.object(monitor.subprocess,'Popen',wraps=monitor.subprocess.Popen) as start:
            code,output=self.run_monitor('status')
        self.assertEqual(code,0)
        self.assertIn('READINESS SNAPSHOT PASSED',output)
        self.assertIn('does NOT mean installation completed',output)
        self.assertTrue(all('openshift-install' not in str(c) for c in start.call_args_list))

    def test_not_ready_snapshot_has_explicit_final_result(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),patch.object(monitor,'snapshot',return_value=False):
            code=monitor.monitor(str(self.root),str(self.root/'sno/auth/kubeconfig'),'cluster.invalid','192.0.2.20','5','10','status')
        self.assertEqual(code,1)
        self.assertIn('RESULT: INSTALLATION NOT READY',output.getvalue())
        self.assertIn('Returning to the shell does NOT mean installation completed',output.getvalue())
        self.assertIn('WITHOUT --status-only',output.getvalue())

    def test_wrong_cluster_is_rejected(self):
        (self.root/'cluster-domain').write_text('other.invalid')
        with self.assertRaises(ValueError): self.run_monitor()

if __name__=='__main__':unittest.main()
