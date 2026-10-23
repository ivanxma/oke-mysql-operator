"""Replacement refuses live/wrong-scope VMs and preserves image-bound auth."""
import importlib.util
import json
from pathlib import Path
import tempfile
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


if __name__ == '__main__': unittest.main()
