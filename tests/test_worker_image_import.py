"""Worker-image import must fail before cloud mutation when scope is wrong."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


class WorkerImageImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.root = self.work / 'worker-image'
        self.root.mkdir()
        self.media = b'example installation media'
        digest = hashlib.sha256(self.media).hexdigest()
        (self.root / 'okd-worker-oci.iso').write_bytes(self.media)
        (self.root / 'okd-worker-oci.iso.sha256').write_text(digest + '  okd-worker-oci.iso\n')
        self.build = dict(role='worker', architecture='x86_64', cluster_domain='cluster.test', version='test-release')
        self.write_build()
        self.sdk = MagicMock()
        self.identity = self.sdk.identity.IdentityClient.return_value
        self.identity.get_compartment.return_value.data = types.SimpleNamespace(name='Allowed', lifecycle_state='ACTIVE')
        self.storage = self.sdk.object_storage.ObjectStorageClient.return_value
        self.storage.get_bucket.return_value.data = types.SimpleNamespace(compartment_id='allowed-id', public_access_type='NoPublicAccess')
        self.storage.head_object.return_value.headers = {'Content-Length': str(len(self.media)), 'ETag': 'test-etag'}
        self.storage.get_object.return_value.data.raw.stream.return_value = [self.media]
        self.compute = self.sdk.core.ComputeClient.return_value
        self.compute.get_image.return_value.data = types.SimpleNamespace(id='image-id', compartment_id='allowed-id', lifecycle_state='AVAILABLE')
        self.compute.create_image.return_value.data = self.compute.get_image.return_value.data
        self.sdk.pagination.list_call_get_all_results.return_value.data = []
        self.compute.list_compute_global_image_capability_schemas.return_value.data = [types.SimpleNamespace(current_version_name='v1')]
        data = {k: types.SimpleNamespace(values=[v], default_value=v) for k, v in {
            'Compute.Firmware': 'UEFI_64', 'Compute.LaunchMode': 'PARAVIRTUALIZED',
            'Network.AttachmentType': 'PARAVIRTUALIZED', 'Storage.BootVolumeType': 'PARAVIRTUALIZED'}.items()}
        data['Compute.SecureBoot'] = types.SimpleNamespace(default_value=False)
        schema = types.SimpleNamespace(id='schema-id', compartment_id='allowed-id', image_id='image-id', schema_data=data)
        self.compute.create_compute_image_capability_schema.return_value.data = schema
        self.compute.get_compute_image_capability_schema.return_value.data = schema
        path = Path(__file__).resolve().parents[1] / 'openshift/community/lib/import-oci-image.py'
        spec = importlib.util.spec_from_file_location('worker_import_under_test', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'oci': self.sdk}):
            spec.loader.exec_module(self.module)
        self.args = ['helper', str(self.work), 'allowed-id', 'Allowed', 'region-test', 'ns-test', 'private-bucket', 'cluster.test', 'test-release']

    def write_build(self):
        (self.root / 'build.json').write_text(json.dumps(self.build))

    def run_import(self):
        with patch.object(sys, 'argv', self.args), contextlib.redirect_stdout(io.StringIO()):
            self.module.main()

    def refuse(self, message):
        with self.assertRaisesRegex(RuntimeError, message):
            self.run_import()
        self.compute.create_image.assert_not_called()
        self.sdk.object_storage.UploadManager.assert_not_called()

    def test_wrong_role(self):
        self.build['role'] = 'master'
        self.write_build()
        self.refuse('does not match')

    def test_tampered_media(self):
        (self.root / 'okd-worker-oci.iso').write_bytes(b'changed')
        self.refuse('checksum mismatch')

    def test_wrong_compartment_identity(self):
        self.identity.get_compartment.return_value.data.name = 'Other'
        self.refuse('identity/state mismatch')

    def test_bucket_outside_compartment(self):
        self.storage.get_bucket.return_value.data.compartment_id = 'other-id'
        self.refuse('private and inside')

    def test_public_bucket(self):
        self.storage.get_bucket.return_value.data.public_access_type = 'ObjectRead'
        self.refuse('private and inside')

    def test_remote_object_content_mismatch(self):
        self.storage.get_object.return_value.data.raw.stream.return_value = [b'tampered']
        self.refuse('SHA-256 does not match')

    def test_import_and_resume_do_not_create_duplicate_image(self):
        self.run_import()
        self.run_import()
        self.compute.create_image.assert_called_once()
        self.compute.create_compute_image_capability_schema.assert_called_once()
        self.sdk.auth.signers.InstancePrincipalsSecurityTokenSigner.assert_called()
        state = json.loads((self.root / 'oci-worker-image.json').read_text())
        self.assertTrue(state['uefi_configured'])
        self.assertEqual(state['image_state'], 'AVAILABLE')

    def test_control_plane_import_uses_separate_media_and_state(self):
        self.build['role'] = 'control-plane'
        (self.work / 'control-plane-build.json').write_text(json.dumps(self.build))
        (self.work / 'okd-sno-oci.iso').write_bytes(self.media)
        digest = hashlib.sha256(self.media).hexdigest()
        (self.work / 'okd-sno-oci.iso.sha256').write_text(digest + '  okd-sno-oci.iso\n')
        self.args.append('control-plane')
        self.run_import()
        state = json.loads((self.work / 'oci-control-plane-image.json').read_text())
        self.assertTrue(state['uefi_configured'])
        self.assertFalse((self.root / 'oci-worker-image.json').exists())

    def test_resume_cannot_change_compartment(self):
        self.run_import()
        self.compute.create_image.reset_mock()
        self.args[2] = 'different-compartment'
        self.refuse('different media or OCI scope')


if __name__ == '__main__':
    unittest.main()
