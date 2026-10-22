"""Bucket creation, scope guards and generated environment; no cloud access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bucket_prepare', ROOT/'openshift/community/lib/prepare-bucket.py')
bucket = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bucket)


class ServiceError(Exception):
    def __init__(self, status):
        self.status = status


class BucketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.sdk = MagicMock()
        self.sdk.exceptions.ServiceError = ServiceError
        self.identity = self.sdk.identity.IdentityClient.return_value
        self.identity.get_compartment.return_value.data = types.SimpleNamespace(
            id='ocid1.compartment.allowed', name='Allowed', lifecycle_state='ACTIVE')
        self.storage = self.sdk.object_storage.ObjectStorageClient.return_value
        self.storage.get_namespace.return_value.data = 'synthetic-namespace'
        self.good = types.SimpleNamespace(compartment_id='ocid1.compartment.allowed', name='synthetic-bucket',
                                          public_access_type='NoPublicAccess', storage_tier='Standard')
        self.storage.get_bucket.return_value.data = self.good
        self.args = [str(self.work), 'ocid1.compartment.allowed', 'Allowed', 'uk-london-1',
                     'synthetic-namespace', 'synthetic-bucket', 'okd-test']
        self.record = dict(compartment_id=self.args[1], compartment_name=self.args[2], region=self.args[3],
                           namespace=self.args[4], bucket=self.args[5], phase='verified', admin_workdir=str(self.work), cluster='okd-test')

    def provision(self):
        with patch.dict(sys.modules, {'oci':self.sdk}):
            return bucket.provision(*self.args)

    def test_existing_private_bucket_reused_without_modification(self):
        record = self.provision()
        self.assertEqual(record['phase'], 'verified')
        self.storage.create_bucket.assert_not_called()
        self.storage.update_bucket.assert_not_called()
        for name in ('oci-bucket.json', 'oci-bucket.env'):
            self.assertEqual((self.work/name).stat().st_mode & 0o777, 0o600)
        client_options = self.sdk.object_storage.ObjectStorageClient.call_args
        self.assertEqual(client_options.args[0], {'region':'uk-london-1'})
        self.assertIn('signer',client_options.kwargs)

    def test_missing_bucket_created_private_in_authorized_compartment(self):
        self.storage.get_bucket.side_effect = [ServiceError(404), types.SimpleNamespace(data=self.good)]
        self.provision()
        self.storage.create_bucket.assert_called_once()
        details = self.sdk.object_storage.models.CreateBucketDetails.call_args.kwargs
        self.assertEqual(details['compartment_id'], self.args[1])
        self.assertEqual(details['public_access_type'],'NoPublicAccess')
        self.assertEqual(details['storage_tier'],'Standard')

    def test_generated_name_is_reused_after_interrupted_creation(self):
        self.args[4] = self.args[5] = ''
        self.storage.get_bucket.side_effect = ServiceError(404)
        self.storage.create_bucket.side_effect = ServiceError(403)
        with self.assertRaises(ServiceError): self.provision()
        saved=json.loads((self.work/'oci-bucket.json').read_text())
        self.assertTrue(saved['bucket'].startswith('okd-test-install-'))
        self.assertFalse((self.work/'oci-bucket.env').exists())
        self.storage.get_bucket.side_effect = None
        self.good.name=saved['bucket']
        self.storage.create_bucket.side_effect = None
        record=self.provision()
        self.assertEqual(record['bucket'],saved['bucket'])
        self.assertEqual(record['namespace'],'synthetic-namespace')

    def test_wrong_compartment_refused_before_storage_calls(self):
        self.identity.get_compartment.return_value.data.name='Other'
        with self.assertRaisesRegex(ValueError,'identity'): self.provision()
        self.sdk.object_storage.ObjectStorageClient.assert_not_called()

    def test_wrong_namespace_refused_without_create(self):
        self.args[4]='other-namespace'
        with self.assertRaisesRegex(ValueError,'tenancy'): self.provision()
        self.storage.create_bucket.assert_not_called()

    def test_public_wrong_compartment_or_archive_bucket_refused(self):
        for attribute,value in [('public_access_type','ObjectRead'),('compartment_id','ocid1.compartment.other'),('storage_tier','Archive')]:
            with self.subTest(attribute=attribute):
                previous=getattr(self.good,attribute); setattr(self.good,attribute,value)
                with self.assertRaisesRegex(ValueError,'Bucket must be private'): self.provision()
                setattr(self.good,attribute,previous)
                self.storage.create_bucket.assert_not_called()
                self.storage.update_bucket.assert_not_called()

    def test_get_denied_does_not_try_creation_or_another_authentication(self):
        self.storage.get_bucket.side_effect=ServiceError(403)
        with self.assertRaises(ServiceError): self.provision()
        self.storage.create_bucket.assert_not_called()
        self.sdk.auth.signers.InstancePrincipalsSecurityTokenSigner.assert_called_once()

    def test_concurrent_bucket_creation_revalidated(self):
        self.storage.get_bucket.side_effect=[ServiceError(404),types.SimpleNamespace(data=self.good)]
        self.storage.create_bucket.side_effect=ServiceError(409)
        self.assertEqual(self.provision()['phase'],'verified')

    def test_wrong_saved_region_refused_before_clients(self):
        (self.work/'oci-bucket.json').write_text(json.dumps({**self.record,'region':'eu-frankfurt-1'}))
        with self.assertRaisesRegex(ValueError,'region'): self.provision()
        self.sdk.auth.signers.InstancePrincipalsSecurityTokenSigner.assert_not_called()

    def test_registration_and_environment_scoped_to_region(self):
        state=self.work/'local-state'
        bucket.register(self.record,state,*self.args[1:6])
        envfile=state/'bucket.env'
        self.assertEqual(envfile.stat().st_mode & 0o777,0o600)
        base={**os.environ,'OCI_REGION':'uk-london-1','OCI_COMPARTMENT_ID':self.args[1], 'OCI_BUCKET':'','OCI_NAMESPACE':''}
        result=subprocess.run(['bash','-c','source "$1"; printf "%s" "$OCI_BUCKET"','--',str(envfile)],env=base,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'synthetic-bucket')
        result=subprocess.run(['bash','-c','source "$1"','--',str(envfile)],env={**base,'OCI_REGION':'eu-frankfurt-1'},capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('OCI_REGION',result.stderr)

    def test_registration_rejects_wrong_scope_and_unverified_receipt(self):
        for record in ({**self.record,'phase':'planned'}, {**self.record,'compartment_id':'ocid1.compartment.other'}):
            with self.assertRaises(ValueError): bucket.register(record,self.work/'state',*self.args[1:6])
        self.assertFalse((self.work/'state/bucket.env').exists())

    def test_register_command_generates_local_env_without_importing_oci(self):
        receipt=self.work/'receipt.json';receipt.write_text(json.dumps(self.record))
        state=self.work/'local-state'
        result=subprocess.run([sys.executable,str(ROOT/'openshift/community/lib/prepare-bucket.py'),
                               '--register',str(receipt),str(state),*self.args[1:6],str(self.work)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((state/'bucket.json').read_text())['region'],'uk-london-1')
        self.assertTrue((state/'bucket.env').exists())

    def test_wrong_workspace_receipt_not_registered(self):
        with self.assertRaisesRegex(ValueError,'workspace'):
            bucket.register(self.record,self.work/'state',*self.args[1:6],'/other/work')
        self.assertFalse((self.work/'state/bucket.env').exists())

    def test_generated_environment_rejects_conflicting_manual_bucket(self):
        envfile=self.work/'bucket.env';envfile.write_text(bucket.environment(self.record))
        result=subprocess.run(['bash','-c','source "$1"','--',str(envfile)],
                              env={**os.environ,'OCI_REGION':'uk-london-1','OCI_COMPARTMENT_ID':self.args[1],
                                   'OCI_BUCKET':'other-bucket','OCI_NAMESPACE':''},capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('OCI_BUCKET',result.stderr)

    def test_scripts_syntax_and_help_are_local(self):
        for path in (ROOT/'openshift/community/02-create-bucket.sh',ROOT/'openshift/community/01-prepare-host.sh'):
            subprocess.run(['bash','-n',str(path)],check=True)
        result=subprocess.run(['bash',str(ROOT/'openshift/community/02-create-bucket.sh'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)


if __name__ == '__main__':
    unittest.main()
