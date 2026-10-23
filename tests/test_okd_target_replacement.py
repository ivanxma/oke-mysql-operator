"""Replacement refuses live/wrong-scope VMs and preserves image-bound auth."""
import importlib.util
import json
from pathlib import Path
import tempfile
import os
import subprocess
import shutil
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('replacement', ROOT / 'openshift/community/lib/replace-terminated-target.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'admin'; self.state = self.root / 'state'
        self.work.mkdir(); self.state.mkdir()
        self.scope = dict(region='test-region', compartment_id='compartment-test', admin_workdir=str(self.work),
                          cluster_name='okd-test', base_domain='lab.test', machine_cidr='192.0.2.0/24')
        self.old = Obj(id='old-instance', compartment_id='compartment-test', lifecycle_state='TERMINATED', image_id='image-test')
        self.new = Obj(id='new-instance', compartment_id='compartment-test', lifecycle_state='RUNNING', image_id='image-test',
                       display_name='replacement', shape='shape-test', shape_config=Obj(ocpus=4, memory_in_gbs=48))
        self.subnet = Obj(id='subnet-test', compartment_id='compartment-test', cidr_block='192.0.2.0/24', lifecycle_state='AVAILABLE')
        self.vnic = Obj(id='vnic-test', compartment_id='compartment-test', subnet_id='subnet-test', public_ip=None)
        self.compute = Obj(get_instance=lambda identity: Obj(data=self.old if identity == 'old-instance' else self.new),
                           list_vnic_attachments=lambda cid: Obj(data=[Obj(vnic_id='vnic-test', lifecycle_state='ATTACHED', instance_id='new-instance')]))
        self.network = Obj(list_subnets=lambda cid: Obj(data=[self.subnet]),
                           list_private_ips=lambda **kw: Obj(data=[Obj(vnic_id='vnic-test')]),
                           get_vnic=lambda identity: Obj(data=self.vnic))
        self.save(self.work / 'oci-control-plane-image.json', dict(compartment_id='compartment-test', region='test-region', image_state='AVAILABLE', image_id='image-test'))
        self.save(self.work / 'control-plane-build.json', dict(cluster_domain='okd-test.lab.test', machine_cidr='192.0.2.0/24'))
        self.current = dict(self.scope, node_ip='192.0.2.20')
        self.save(self.state / 'node-ip.json', self.current)
        self.save(self.work / 'oci-node-ip.json', self.current)
        self.save(self.state / 'verified-target.json', dict(instance_id='old-instance'))

    def save(self, path, value):
        path.write_text(json.dumps(value))

    def verify(self):
        return module.verify(str(self.work), str(self.state), 'compartment-test', 'test-region', 'okd-test',
                             'lab.test', '192.0.2.0/24', '192.0.2.30', 'old-instance', self.compute, self.network,
                             lambda call, *a, **kw: call(*a, **kw))

    def test_cloud_verified_preview_does_not_change_registration(self):
        before = (self.state / 'node-ip.json').read_text()
        record, current = self.verify()
        self.assertEqual(record['private_ip'], '192.0.2.30')
        self.assertEqual(current, self.current)
        self.assertEqual((self.state / 'node-ip.json').read_text(), before)
        self.assertFalse((self.state / 'history').exists())

    def test_refuses_unterminated_previous_or_wrong_compartment(self):
        for state, cid in [('RUNNING', 'compartment-test'), ('TERMINATED', 'other-compartment')]:
            self.old.lifecycle_state = state; self.old.compartment_id = cid
            with self.subTest(state=state, cid=cid), self.assertRaises(ValueError): self.verify()

    def test_refuses_public_wrong_image_and_same_instance_replacement(self):
        self.vnic.public_ip = '198.51.100.1'
        with self.assertRaises(ValueError): self.verify()
        self.vnic.public_ip = None; self.new.image_id = 'wrong-image'
        with self.assertRaises(ValueError): self.verify()
        self.new.image_id = 'image-test'; self.new.id = self.old.id
        with self.assertRaises(ValueError): self.verify()

    def test_refuses_ambiguous_subnet_and_mismatched_registration(self):
        self.network.list_subnets = lambda cid: Obj(data=[self.subnet, self.subnet])
        with self.assertRaises(ValueError): self.verify()
        self.network.list_subnets = lambda cid: Obj(data=[self.subnet])
        self.save(self.work / 'oci-node-ip.json', dict(self.current, node_ip='192.0.2.21'))
        with self.assertRaises(ValueError): self.verify()

    def test_archives_stale_dns_and_completion_preserves_image_auth(self):
        record, current = self.verify()
        (self.work / 'oci-dns-view.json').write_text('old discovery')
        (self.state / 'installation-acceptance.json').write_text('old acceptance')
        (self.state / 'known_hosts').write_text('existing hosts')
        (self.work / 'sno/auth').mkdir(parents=True)
        (self.work / 'sno/auth/kubeconfig').write_text('image credentials')
        (self.work / 'sno/.openshift_install_state.json').write_text('image metadata')
        with patch.object(module.subprocess, 'run') as command:
            module.archive_state(str(self.work), str(self.state), current, record)
        command.assert_called_once()
        self.assertIn('192.0.2.20', command.call_args.args[0])
        self.assertFalse((self.work / 'oci-dns-view.json').exists())
        self.assertFalse((self.state / 'installation-acceptance.json').exists())
        self.assertEqual((self.work / 'sno/auth/kubeconfig').read_text(), 'image credentials')
        self.assertEqual((self.work / 'sno/.openshift_install_state.json').read_text(), 'image metadata')
        self.assertEqual((self.state / 'verified-target.json').stat().st_mode & 0o777, 0o600)
        archive = next((self.state / 'history').iterdir())
        self.assertTrue((archive / 'installation-acceptance.json').exists())
        self.assertTrue((archive / 'oci-dns-view.json').exists())

    def test_standard_registration_without_optional_inventory_can_be_replaced(self):
        (self.state / 'verified-target.json').unlink()
        record, current = self.verify()
        self.assertFalse(record['registration_current'])
        module.archive_state(str(self.work), str(self.state), current, record)
        self.assertEqual(json.loads((self.state / 'verified-target.json').read_text())['instance_id'], 'new-instance')

    def test_previous_instance_discovered_from_verified_private_inventory(self):
        self.save(self.state / 'verified-target.json', dict(instance_id='old-instance',compartment_id='compartment-test',region='test-region'))
        scope={k:self.scope[k] for k in ('admin_workdir','compartment_id','region','cluster_name','base_domain')}
        self.assertEqual(module.previous_instance(str(self.state),'',scope,'192.0.2.30'),'old-instance')

    def test_retry_loads_original_previous_instance_from_matching_receipt(self):
        scope={k:self.scope[k] for k in ('admin_workdir','compartment_id','region','cluster_name','base_domain')}
        self.save(self.state / 'target-replacement.json',dict(scope,node_ip='192.0.2.30',previous_instance_id='old-instance'))
        self.save(self.state / 'verified-target.json',dict(instance_id='new-instance',compartment_id='compartment-test',region='test-region'))
        self.assertEqual(module.previous_instance(str(self.state),'',scope,'192.0.2.30'),'old-instance')
        self.assertEqual(module.previous_instance(str(self.state),'',scope,'192.0.2.40'),'new-instance')

    def test_other_scope_receipt_is_not_used_for_automatic_selection(self):
        scope={k:self.scope[k] for k in ('admin_workdir','compartment_id','region','cluster_name','base_domain')}
        self.save(self.state / 'target-replacement.json',dict(scope,region='wrong-region',node_ip='192.0.2.30',previous_instance_id='old-instance'))
        with self.assertRaises(ValueError):module.previous_instance(str(self.state),'',scope,'192.0.2.30')

    def test_same_address_replacement_still_archives_old_instance_state(self):
        same = dict(self.scope, node_ip='192.0.2.30')
        self.save(self.state / 'node-ip.json', same); self.save(self.work / 'oci-node-ip.json', same)
        record, current = self.verify()
        self.assertFalse(record['registration_current'])
        module.archive_state(str(self.work), str(self.state), current, record)
        self.assertTrue((self.state / 'history').exists())
        self.assertEqual(json.loads((self.state / 'verified-target.json').read_text())['instance_id'], 'new-instance')

    def test_idempotent_retry_keeps_current_acceptance(self):
        record, _ = self.verify()
        current = dict(self.scope, node_ip='192.0.2.30')
        self.save(self.state / 'node-ip.json', current); self.save(self.work / 'oci-node-ip.json', current)
        self.save(self.state / 'verified-target.json', record)
        record, current = self.verify()
        (self.state / 'installation-acceptance.json').write_text('current')
        module.archive_state(str(self.work), str(self.state), current, record)
        self.assertEqual((self.state / 'installation-acceptance.json').read_text(), 'current')
        self.assertFalse((self.state / 'history').exists())

    def test_symlink_state_refused_before_archiving(self):
        record, current = self.verify()
        external = self.root / 'external'; external.write_text('preserve')
        (self.work / 'oci-dns-view.json').symlink_to(external)
        with self.assertRaises(ValueError): module.archive_state(str(self.work), str(self.state), current, record)
        self.assertTrue((self.state / 'node-ip.json').exists())
        self.assertEqual(external.read_text(), 'preserve')


class CompletionSequenceTests(unittest.TestCase):
    """Run the actual shell entry point against harmless step executables."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); (self.root/'lib').mkdir()
        self.log=self.root/'steps'
        shutil.copy2(ROOT/'openshift/community/05-replace-terminated-target.sh',self.root/'05-replace-terminated-target.sh')
        (self.root/'lib/common.sh').write_text('''COMMUNITY_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fail() { echo "$*" >&2; exit 1; }
load_config() { ADMIN_HOST=127.0.0.1; ADMIN_WORKDIR=$COMMUNITY_DIR; STATE_DIR=$COMMUNITY_DIR; OCI_COMPARTMENT_ID=synthetic; OCI_REGION=test-region; CLUSTER_NAME=test; BASE_DOMAIN=lab.test; MACHINE_CIDR=192.0.2.0/24; }
''')
        (self.root/'lib/replace-terminated-target.py').write_text('print("cloud verification stub")\n')
        self.names=['05-register-node-ip.sh','01-prepare-host.sh','05-discover-dns-view.sh','05-configure-dns.sh',
                    '05-verify-dns.sh','00-validate-environment.sh','05-wait-for-control-plane.sh',
                    '05-diagnose-control-plane.sh','06-check-control-plane.sh','11-verify-web-access.sh']
        for name in self.names:self.step(name,0)

    def step(self,name,code):
        (self.root/name).write_text('echo "'+name+' $*" >> "$STEP_LOG"\nexit '+str(code)+'\n')

    def run_script(self,*flags):
        return subprocess.run(['bash',str(self.root/'05-replace-terminated-target.sh'),'192.0.2.30',
            '--previous-instance-id','ocid1.instance.synthetic',*flags],env=dict(os.environ,STEP_LOG=str(self.log)),capture_output=True,text=True)

    def test_complete_runs_acceptance_only_after_successful_monitor(self):
        result=self.run_script('--apply','--complete')
        self.assertEqual(result.returncode,0,result.stderr)
        steps=self.log.read_text()
        self.assertLess(steps.index('05-wait-for-control-plane.sh'),steps.index('06-check-control-plane.sh'))
        self.assertLess(steps.index('06-check-control-plane.sh'),steps.index('11-verify-web-access.sh'))
        self.assertIn('COMPLETE:',result.stdout)

    def test_failed_monitor_collects_evidence_without_acceptance_or_success_claim(self):
        self.step('05-wait-for-control-plane.sh',124)
        result=self.run_script('--apply','--complete')
        self.assertEqual(result.returncode,124)
        steps=self.log.read_text()
        self.assertIn('05-diagnose-control-plane.sh --node-bootstrap',steps)
        self.assertNotIn('06-check-control-plane.sh',steps)
        self.assertNotIn('COMPLETE:',result.stdout)

    def test_preflight_failure_cannot_be_acknowledged_as_warning(self):
        self.step('00-validate-environment.sh',1)
        result=self.run_script('--apply','--complete','--allow-preflight-warnings')
        self.assertEqual(result.returncode,1)
        self.assertNotIn('05-wait-for-control-plane.sh',self.log.read_text())

    def test_preflight_warnings_require_explicit_acknowledgement(self):
        self.step('00-validate-environment.sh',2)
        result=self.run_script('--apply','--complete')
        self.assertEqual(result.returncode,2)
        self.assertNotIn('05-wait-for-control-plane.sh',self.log.read_text())
        self.log.unlink()
        result=self.run_script('--apply','--complete','--allow-preflight-warnings')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('external access remains unverified',result.stdout)

    def test_preview_does_not_run_any_deployment_steps(self):
        result=self.run_script()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(self.log.exists())

    def test_failed_acceptance_does_not_verify_console_or_claim_complete(self):
        self.step('06-check-control-plane.sh',1)
        result=self.run_script('--apply','--complete')
        self.assertEqual(result.returncode,1)
        self.assertNotIn('11-verify-web-access.sh',self.log.read_text())
        self.assertNotIn('COMPLETE:',result.stdout)


if __name__ == '__main__': unittest.main()
