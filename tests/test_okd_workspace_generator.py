"""Private workspace generation, relocation and safe reruns; no cloud/SSH calls."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('workspace_generator',ROOT/'openshift/community/lib/create-workspace.py')
gen=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen)

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='okd generated clone ')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        shared=self.root/'openshift/community';shared.mkdir(parents=True)
        shutil.copytree(ROOT/'openshift/community/regions',shared/'regions',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT/'openshift/community/env.sh',shared/'env.sh')
        for name in ('community.env.example','community.optional.env.example'):shutil.copy2(ROOT/'openshift/community'/name,shared/name)
        target=shared/'01-prepare-host.sh'
        target.write_text('#!/usr/bin/env bash\npython3 -c \'import os,json; print(json.dumps({k:os.environ.get(k) for k in ("OKD_CONFIG_FILE","OKD_STATE_DIR","KUBECONFIG","NODE_IP")}))\'\n')
        target.chmod(0o700)
        self.key=self.root/'existing key';self.key.write_text('synthetic-key-content');self.key.chmod(0o600)
        self.args=['--region','uk-london-1','--non-interactive','--ssh-key',str(self.key),'--base-domain','lab.test',
                   '--machine-cidr','192.0.2.0/24','--compartment-id','ocid1.compartment.synthetic','--compartment-name','Synthetic',
                   '--admin-workdir','/home/testadmin/new-okd']

    def create(self, extra=()):
        options=gen.parser().parse_args(self.args+list(extra))
        with patch.object(gen,'ROOT',self.root),contextlib.redirect_stdout(io.StringIO()):
            return gen.generate(options,info={})

    def test_generates_executable_folder_without_copying_key(self):
        folder=self.create()
        self.assertEqual(folder,self.root/'generated/openshift/uk-london-1/jumphost')
        self.assertEqual(folder.stat().st_mode&0o777,0o700)
        self.assertEqual((folder/'community.env').stat().st_mode&0o777,0o600)
        self.assertTrue(os.access(folder/'01-prepare-host.sh',os.X_OK))
        self.assertNotIn('synthetic-key-content',(folder/'community.env').read_text())
        self.assertNotIn('NODE_IP=',(folder/'community.env').read_text())
        self.assertFalse((self.root/'internal_doc').exists())

    def test_generated_offline_checker_accepts_new_private_location(self):
        folder=self.create()
        env={k:v for k,v in os.environ.items() if not k.startswith(('OKD_','MYSQL_'))}
        result=subprocess.run(['bash',str(folder/'00-check-config.sh'),'--preparation'],
                              env=env,capture_output=True,text=True,cwd='/private/tmp')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS: Local regional configuration',result.stdout)
        self.assertFalse((self.root/'internal_doc').exists())

    def test_legacy_profile_is_not_copied_or_overwritten(self):
        folder=self.create()
        legacy=self.root/'internal_doc/regions/uk-london-1/jumphost'
        legacy.parent.mkdir(parents=True)
        shutil.move(str(folder),str(legacy))
        original=(legacy/'community.env').read_bytes()
        with self.assertRaisesRegex(ValueError,'cluster DNS identity|Cluster DNS identity'):
            self.create()
        self.assertEqual((legacy/'community.env').read_bytes(),original)
        self.assertFalse(folder.exists())

    def test_launcher_selects_generated_profile_after_checkout_relocation(self):
        folder=self.create()
        env={k:v for k,v in os.environ.items() if not k.startswith(('OKD_','MYSQL_'))}
        env.update(KUBECONFIG='/stale/config',NODE_IP='198.51.100.20')
        result=subprocess.run(['bash',str(folder/'01-prepare-host.sh')],env=env,capture_output=True,text=True,check=True,cwd='/private/tmp')
        values=json.loads(result.stdout)
        self.assertEqual(values['OKD_CONFIG_FILE'],str(folder/'community.env'))
        self.assertEqual(values['OKD_STATE_DIR'],str(folder/'.state'))
        self.assertEqual(values['KUBECONFIG'],'/home/testadmin/new-okd/sno/auth/kubeconfig')
        self.assertIsNone(values['NODE_IP'])

    def test_existing_profile_and_install_state_preserved(self):
        folder=self.create();profile=(folder/'community.env').read_bytes()
        state=folder/'.state';state.mkdir();secret=state/'protected.txt';secret.write_text('keep')
        self.assertEqual(self.create(),folder)
        self.assertEqual((folder/'community.env').read_bytes(),profile)
        self.assertEqual(secret.read_text(),'keep')

    def test_modified_launcher_not_overwritten(self):
        folder=self.create();target=folder/'01-prepare-host.sh';target.write_text('user modification')
        with self.assertRaisesRegex(ValueError,'overwritten'):self.create()
        self.assertEqual(target.read_text(),'user modification')

    def test_insecure_key_rejected_before_folder_creation(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'SSH key'):self.create()
        self.assertFalse((self.root/'generated').exists())
        self.assertFalse((self.root/'internal_doc').exists())

    def test_network_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError,'must not overlap'):self.create(['--pod-cidr','192.0.2.0/24'])

    def test_duplicate_saved_overlay_rejected(self):
        self.create()
        with self.assertRaisesRegex(ValueError,'saved deployment'):self.create(['--name','another'])

    def test_state_from_another_deployment_rejected(self):
        folder=self.create();optional=folder/'community.optional.env';optional.write_text('OKD_STATE_DIR=/home/testadmin/other-state\n')
        with self.assertRaisesRegex(ValueError,'outside this generated folder'):self.create()

    def test_path_traversal_name_rejected(self):
        with self.assertRaisesRegex(ValueError,'Deployment name'):self.create(['--name','../../escape'])

    def test_symlink_folder_rejected(self):
        parent=self.root/'generated/openshift/uk-london-1';parent.mkdir(parents=True)
        (parent/'jumphost').symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):self.create()

    def test_metadata_region_and_compartment_do_not_require_typing(self):
        options=gen.parser().parse_args(self.args)
        options.region=None; options.compartment_id=None
        with patch.object(gen,'ROOT',self.root),patch('builtins.input',side_effect=AssertionError('Unexpected prompt')),contextlib.redirect_stdout(io.StringIO()):
            folder=gen.generate(options,info={'canonicalRegionName':'eu-frankfurt-1','region':'legacy','compartmentId':'ocid1.compartment.discovered'})
        self.assertIn('eu-frankfurt-1',str(folder))
        data=gen.load_profile(folder/'community.env')
        self.assertEqual(data['OCI_COMPARTMENT_ID'],'ocid1.compartment.discovered')

    def test_new_source_step_is_added_without_resetting_existing_config(self):
        folder=self.create();original=(folder/'community.env').read_bytes()
        step=self.root/'openshift/community/02-create-bucket.sh';step.write_text('#!/bin/sh\nexit 0\n')
        self.create()
        self.assertTrue((folder/'02-create-bucket.sh').is_file())
        self.assertEqual((folder/'community.env').read_bytes(),original)

    def test_supplied_profile_is_adopted_without_replacing_existing_helper(self):
        folder=self.create();original=(folder/'community.env').read_bytes()
        for path in folder.iterdir():
            if path.name not in ('community.env','community.optional.env'):path.unlink()
        helper=folder/'env.sh';helper.write_text('# supplied private helper\n')
        self.create()
        self.assertEqual(helper.read_text(),'# supplied private helper\n')
        self.assertEqual((folder/'community.env').read_bytes(),original)
        self.assertTrue((folder/'00-env.sh').is_file())

    def test_symlink_state_cannot_share_another_deployment(self):
        folder=self.create()
        other=self.root/'other-state';other.mkdir()
        (folder/'.state').symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink configuration or deployment state'):self.create()

    def test_missing_inputs_fail_without_creating_files(self):
        options=gen.parser().parse_args(['--region','uk-london-1','--non-interactive'])
        with patch.object(gen,'ROOT',self.root),self.assertRaisesRegex(ValueError,'Missing SSH'):
            gen.generate(options,info={})
        self.assertFalse((self.root/'generated').exists())
        self.assertFalse((self.root/'internal_doc').exists())

if __name__=='__main__':unittest.main()
