"""API-independent bootstrap evidence: target binding, redaction and interpretation."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('node_bootstrap',Path(__file__).resolve().parents[1]/'openshift/community/lib/node-bootstrap-diagnostics.py')
node=importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)


class NodeProbeTests(unittest.TestCase):
    def metadata(self,address='192.0.2.20',compartment='ocid1.compartment.synthetic'):
        opener=Mock()
        opener.open.side_effect=lambda request,timeout:io.BytesIO(json.dumps(
            [{'privateIp':address}] if request.full_url.endswith('/vnics/') else {'compartmentId':compartment}).encode())
        return patch.object(node.urllib.request,'build_opener',return_value=opener)

    def test_wrong_address_stops_before_privileged_commands(self):
        with self.metadata(address='192.0.2.99'),patch.object(node,'command') as command:
            with self.assertRaisesRegex(ValueError,'NODE_IP'):
                node.probe('192.0.2.20','ocid1.compartment.synthetic')
        command.assert_not_called()

    def test_wrong_compartment_stops_before_privileged_commands(self):
        with self.metadata(compartment='ocid1.compartment.other'),patch.object(node,'command') as command:
            with self.assertRaisesRegex(ValueError,'compartment'):
                node.probe('192.0.2.20','ocid1.compartment.synthetic')
        command.assert_not_called()

    def test_backend_absence_is_reported_without_claiming_readiness(self):
        self.assertIn('etcd listener absent',node.findings('LISTEN 0 4096 *:6443 *:*'))
        self.assertIn('does not prove',node.findings('LISTEN *:6443\nLISTEN *:2379'))

    def test_credential_lines_are_omitted(self):
        text='connection refused localhost:2379\npassword: example\nAuthorization: Bearer example\nserviceaccount token example'
        self.assertEqual(node.redact(text),'connection refused localhost:2379')

    def test_raw_container_metadata_is_not_returned_in_report(self):
        containers={'containers':[{'id':'synthetic-container','createdAt':2,'state':'CONTAINER_RUNNING',
            'metadata':{'name':'etcd'},'annotations':{'secret-note':'not-for-report'}}]}
        def execute(args,**kwargs):
            if args[:3]==['sudo','-n','crictl'] and 'ps' in args:output=json.dumps(containers)
            elif 'ss' in args:output='LISTEN *:6443\nLISTEN *:2379'
            else:output='ordinary progress\npassword: not-for-report'
            return subprocess.CompletedProcess(args,0,output,'')
        with self.metadata(),patch.object(node.subprocess,'run',side_effect=execute):
            report=node.probe('192.0.2.20','ocid1.compartment.synthetic')
        serialized=json.dumps(report)
        self.assertNotIn('not-for-report',serialized)
        self.assertNotIn('annotations',serialized)
        self.assertEqual(report['containers'][1]['name'],'etcd')
        self.assertIn('uptime',report)
        self.assertIn('kubelet',report)
        self.assertIn('static_pods',report)

    def test_command_timeout_is_explicit(self):
        with patch.object(node.subprocess,'run',side_effect=subprocess.TimeoutExpired('command',10)):
            result=node.command(['command'])
        self.assertEqual(result['exit'],1)
        self.assertIn('exceeded',result['output'])

    def test_stale_ssh_target_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'ssh_config').write_text('synthetic')
            with patch.object(node.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'hostname 192.0.2.99\n','')) as run:
                with self.assertRaisesRegex(ValueError,'differs from NODE_IP'):
                    node.collect(temp,'192.0.2.20','ocid1.compartment.synthetic')
            self.assertEqual(run.call_count,1)

    def test_report_is_saved_privately_and_does_not_claim_cluster_ready(self):
        report=dict(address='192.0.2.20',compartment='ocid1.compartment.synthetic',finding='bootstrap pending',
                    services={'output':'active'},containers=[])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'ssh_config').write_text('synthetic')
            output=io.StringIO()
            with patch.object(node.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,'hostname 192.0.2.20\n',''),
                subprocess.CompletedProcess([],0,json.dumps(report),'')]),contextlib.redirect_stdout(output):
                node.collect(temp,'192.0.2.20','ocid1.compartment.synthetic')
            saved=next(root.glob('node-bootstrap-*.json'))
            self.assertEqual(saved.stat().st_mode & 0o777,0o600)
            self.assertIn('not a cluster-readiness result',output.getvalue())

    def test_missing_rotated_log_is_not_presented_as_etcd_fatal_error(self):
        report=dict(address='192.0.2.20',compartment='ocid1.compartment.synthetic',finding='bootstrap pending',
                    services={'output':'active'},containers=[dict(name='etcd',state='CONTAINER_EXITED',
                    logs={'exit':1,'output':'level=fatal msg="failed resolving missing log symlinks"'})])
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'ssh_config').write_text('synthetic')
            output=io.StringIO()
            with patch.object(node.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,'hostname 192.0.2.20\n',''),
                subprocess.CompletedProcess([],0,json.dumps(report),'')]),contextlib.redirect_stdout(output):
                node.collect(temp,'192.0.2.20','ocid1.compartment.synthetic')
            self.assertIn('Log collection unavailable',output.getvalue())
            self.assertNotIn('level=fatal',output.getvalue())


if __name__=='__main__':unittest.main()
