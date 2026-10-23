"""Explicit target registration; never choose an address or call OCI."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('target_registration',ROOT/'openshift/community/lib/register-node-ip.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class TargetRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name)
        self.args=[str(self.work),'ocid1.compartment.synthetic','region-test','okd-test','lab.test','192.0.2.0/24','192.0.2.20']
        (self.work/'env.sh').write_text('export ADMIN_WORKDIR='+str(self.work)+'\nexport OCI_REGION=region-test\nexport OCI_COMPARTMENT_ID=ocid1.compartment.synthetic\nexport KUBECONFIG='+str(self.work/'auth/kubeconfig')+'\n')

    def test_only_supplied_address_is_recorded_and_ingested(self):
        record=module.register(*self.args)
        self.assertEqual(record['node_ip'],'192.0.2.20')
        self.assertIn('not reserved',record['availability'])
        for name in ['oci-node-ip.json','oci-node-ip.env','env.sh']:
            self.assertEqual((self.work/name).stat().st_mode & 0o777,0o600)
        result=subprocess.run(['bash','-c','source "$1/env.sh"; printf "%s|%s" "$NODE_IP" "$KUBECONFIG"','--',str(self.work)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'192.0.2.20|'+str(self.work/'auth/kubeconfig'))

    def test_reserved_or_outside_addresses_refused_without_records(self):
        for value in ['192.0.2.0','192.0.2.1','192.0.2.255','198.51.100.20','not-an-ip','']:
            with self.subTest(value=value),self.assertRaises(ValueError):module.register(*self.args[:-1],value)
        self.assertFalse((self.work/'oci-node-ip.env').exists())

    def test_existing_installer_state_refuses_new_registration(self):
        (self.work/'sno').mkdir()
        original=(self.work/'env.sh').read_text()
        with self.assertRaisesRegex(ValueError,'Installer state'):module.register(*self.args)
        self.assertEqual((self.work/'env.sh').read_text(),original)
        self.assertFalse((self.work/'oci-node-ip.json').exists())

    def test_first_registration_after_matching_image_import_is_allowed(self):
        (self.work/'sno').mkdir()
        (self.work/'control-plane-build.json').write_text(json.dumps(dict(cluster_domain='okd-test.lab.test',role='control-plane',machine_cidr='192.0.2.0/24')))
        (self.work/'oci-control-plane-image.json').write_text(json.dumps(dict(compartment_id='ocid1.compartment.synthetic',region='region-test',image_state='AVAILABLE')))
        self.assertEqual(module.register(*self.args)['node_ip'],'192.0.2.20')

    def test_registration_after_image_import_rejects_other_scope(self):
        (self.work/'sno').mkdir()
        (self.work/'control-plane-build.json').write_text(json.dumps(dict(cluster_domain='okd-test.lab.test',role='control-plane',machine_cidr='192.0.2.0/24')))
        (self.work/'oci-control-plane-image.json').write_text(json.dumps(dict(compartment_id='ocid1.compartment.other',region='region-test',image_state='AVAILABLE')))
        with self.assertRaisesRegex(ValueError,'scope'):module.register(*self.args)
        self.assertFalse((self.work/'oci-node-ip.env').exists())

    def test_existing_registration_cannot_change_address(self):
        module.register(*self.args)
        with self.assertRaisesRegex(ValueError,'different target IP'):module.register(*self.args[:-1],'192.0.2.21')
        self.assertEqual(json.loads((self.work/'oci-node-ip.json').read_text())['node_ip'],'192.0.2.20')

    def test_repeat_same_address_preserves_one_environment_hook(self):
        module.register(*self.args);module.register(*self.args)
        self.assertEqual((self.work/'env.sh').read_text().count('# BEGIN OKD GENERATED TARGET REGISTRATION'),1)

    def test_wrong_region_receipt_refused(self):
        record=module.register(*self.args)
        scope={k:record[k] for k in module.FIELDS};scope['region']='other-region'
        with self.assertRaisesRegex(ValueError,'another region'):module.ingest(record,self.work/'local-state',scope)
        self.assertFalse((self.work/'local-state/node-ip.env').exists())

    def test_generated_environment_rejects_wrong_cluster_before_export(self):
        record=module.register(*self.args);path=self.work/'oci-node-ip.env'
        env={**os.environ,'OCI_REGION':'region-test','OCI_COMPARTMENT_ID':record['compartment_id'],
             'ADMIN_WORKDIR':str(self.work),'CLUSTER_NAME':'different','BASE_DOMAIN':'lab.test','MACHINE_CIDR':'192.0.2.0/24'}
        r=subprocess.run(['bash','-c','source "$1"','--',str(path)],env=env,capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('CLUSTER_NAME',r.stderr)

    def test_scripts_help_is_local_and_requires_no_configuration(self):
        for path in [ROOT/'openshift/community/05-register-node-ip.sh',ROOT/'openshift/community/regions/uk-london-1/05-register-node-ip.sh']:
            subprocess.run(['bash','-n',str(path)],check=True)
            r=subprocess.run(['bash',str(path),'--help'],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
        r=subprocess.run(['bash',str(ROOT/'openshift/community/05-register-node-ip.sh')],capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('explicit private IP',r.stderr)


if __name__=='__main__':unittest.main()
