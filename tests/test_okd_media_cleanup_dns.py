"""Cleanup and DNS scope guards; no cloud access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'openshift/community/lib'/filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

cleanup = module('media_cleanup', 'cleanup-local-iso.py')
dns = module('cluster_dns', 'configure-dns.py')

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root/'okd-sno-oci.iso'
        self.media.write_bytes(b'verified-media')
        self.args = [str(self.root), 'synthetic-compartment', 'Synthetic', 'synthetic-region',
                     'synthetic-namespace', 'synthetic-bucket', 'cluster.invalid', 'test', 'control-plane']
        build = dict(role='control-plane', architecture='x86_64', cluster_domain='cluster.invalid', version='test')
        (self.root/'control-plane-build.json').write_text(json.dumps(build))
        self.state = dict(zip(('compartment_id','compartment_name','region','namespace','bucket'), self.args[1:6]))
        self.state.update(sha256=hashlib.sha256(self.media.read_bytes()).hexdigest(),
                          uefi_configured=True, image_id='synthetic-image', object='media.iso')
        self.save()
        self.sdk = MagicMock()
        self.sdk.identity.IdentityClient.return_value.get_compartment.return_value.data = types.SimpleNamespace(
            id=self.args[1], name=self.args[2], lifecycle_state='ACTIVE')
        self.storage = self.sdk.object_storage.ObjectStorageClient.return_value
        self.storage.get_bucket.return_value.data = types.SimpleNamespace(
            compartment_id=self.args[1], public_access_type='NoPublicAccess')
        self.image = types.SimpleNamespace(compartment_id=self.args[1], lifecycle_state='AVAILABLE')
        self.sdk.core.ComputeClient.return_value.get_image.return_value.data = self.image
        self.storage.get_object.return_value.data.raw.stream.return_value = [b'verified-media']

    def save(self):
        (self.root/'oci-control-plane-image.json').write_text(json.dumps(self.state))

    def run_cleanup(self, action):
        with patch.dict(sys.modules, {'oci':self.sdk}):
            cleanup.cleanup(*self.args, action)

    def test_inspect_does_not_delete(self):
        self.run_cleanup('inspect')
        self.assertTrue(self.media.exists())

    def test_delete_only_media(self):
        (self.root/'scos-live.iso').write_bytes(b'base')
        self.run_cleanup('delete')
        self.assertFalse(self.media.exists())
        self.assertTrue((self.root/'scos-live.iso').exists())
        self.assertTrue((self.root/'oci-control-plane-image.json').exists())

    def test_wrong_scope_refused(self):
        self.state['region'] = 'other-region'; self.save()
        with self.assertRaises(ValueError): self.run_cleanup('delete')
        self.assertTrue(self.media.exists())
        self.storage.get_object.assert_not_called()

    def test_importing_image_refused(self):
        self.image.lifecycle_state = 'IMPORTING'
        with self.assertRaises(ValueError): self.run_cleanup('delete')
        self.assertTrue(self.media.exists())

    def test_wrong_uploaded_bytes_refused(self):
        self.storage.get_object.return_value.data.raw.stream.return_value = [b'corrupt']
        with self.assertRaises(ValueError): self.run_cleanup('delete')
        self.assertTrue(self.media.exists())

    def test_symlink_refused(self):
        other = self.root/'other.iso'; self.media.rename(other); self.media.symlink_to(other)
        with self.assertRaises(ValueError): self.run_cleanup('delete')
        self.assertTrue(other.exists())

class DnsTests(unittest.TestCase):
    def record(self, name, kind='A', data='192.0.2.10'):
        return types.SimpleNamespace(domain=name, rtype=kind, rdata=data, ttl=60)

    def test_missing_records(self):
        ops = dns.plan_records([], 'cluster.invalid', '192.0.2.10')
        self.assertEqual(len(ops), 3)
        self.assertTrue(all(op['operation']=='ADD' for op in ops))

    def test_idempotent(self):
        records = [self.record(p+'cluster.invalid') for p in ('api.', 'api-int.', '*.apps.')]
        self.assertEqual(dns.plan_records(records, 'cluster.invalid', '192.0.2.10'), [])

    def test_only_intended_a_records_changed(self):
        records = [self.record('api.cluster.invalid', data='192.0.2.11'),
                   self.record('unrelated.cluster.invalid'), self.record('api.cluster.invalid','TXT','hello')]
        ops = dns.plan_records(records, 'cluster.invalid', '192.0.2.10')
        self.assertEqual(len(ops), 4)
        self.assertEqual(ops[0]['operation'], 'REMOVE')
        self.assertTrue(all(op['rtype']=='A' for op in ops))
        self.assertFalse(any('unrelated' in op['domain'] for op in ops))

    def test_cname_refused(self):
        with self.assertRaises(ValueError):
            dns.plan_records([self.record('api.cluster.invalid','CNAME','other.invalid')], 'cluster.invalid','192.0.2.10')

class DnsScopeTests(unittest.TestCase):
    def setUp(self):
        self.sdk = MagicMock()
        self.sdk.identity.IdentityClient.return_value.get_compartment.return_value.data = types.SimpleNamespace(
            id='synthetic-compartment', name='Synthetic', lifecycle_state='ACTIVE')
        self.client = self.sdk.dns.DnsClient.return_value
        self.zone = types.SimpleNamespace(id='synthetic-zone', name='cluster.invalid', compartment_id='synthetic-compartment',
                                         scope='PRIVATE', zone_type='PRIMARY', view_id='synthetic-view')
        self.sdk.pagination.list_call_get_all_results.return_value.data = [self.zone]
        self.client.get_zone.return_value.data = self.zone
        self.client.get_zone_records.return_value = types.SimpleNamespace(headers={'etag':'version-1'}, data=types.SimpleNamespace(items=[]))
        self.args = ['unused-work','synthetic-compartment','Synthetic','synthetic-region','cluster.invalid','192.0.2.10']

    def run_dns(self, action='plan', view=''):
        with patch.dict(sys.modules, {'oci':self.sdk}):
            dns.configure(*self.args, action, view)

    def test_default_is_read_only(self):
        self.run_dns()
        self.client.patch_zone_records.assert_not_called()
        self.client.create_zone.assert_not_called()

    def test_read_query_uses_supported_page_size(self):
        self.run_dns()
        self.assertEqual(self.client.get_zone_records.call_args.kwargs['limit'], 100)
        self.assertEqual(self.client.get_zone_records.call_args.kwargs['scope'], 'PRIVATE')

    def test_paginated_zone_stops_before_mutation(self):
        self.client.get_zone_records.return_value.headers['opc-next-page'] = 'more'
        with self.assertRaises(ValueError): self.run_dns('apply')
        self.client.patch_zone_records.assert_not_called()

    def test_foreign_zone_refused(self):
        self.zone.compartment_id = 'other-compartment'
        with self.assertRaises(ValueError): self.run_dns('apply')
        self.client.patch_zone_records.assert_not_called()

    def test_missing_zone_without_view_refused(self):
        self.sdk.pagination.list_call_get_all_results.return_value.data = []
        with self.assertRaises(ValueError): self.run_dns('apply')
        self.client.create_zone.assert_not_called()

    def test_foreign_view_refused(self):
        self.sdk.pagination.list_call_get_all_results.return_value.data = []
        self.client.get_view.return_value.data = types.SimpleNamespace(id='synthetic-view',compartment_id='other')
        with self.assertRaises(ValueError): self.run_dns('apply', 'synthetic-view')
        self.client.create_zone.assert_not_called()

    def test_zone_creation_preview_is_read_only(self):
        self.sdk.pagination.list_call_get_all_results.return_value.data = []
        self.client.get_view.return_value.data = types.SimpleNamespace(id='synthetic-view',compartment_id='synthetic-compartment')
        self.run_dns('plan', 'synthetic-view')
        self.client.create_zone.assert_not_called()

    def test_registered_view_is_automatically_used(self):
        with tempfile.TemporaryDirectory() as work:
            self.args[0] = work
            record = dict(admin_workdir=work, compartment_id='synthetic-compartment', compartment_name='Synthetic',
                          region='synthetic-region', cluster_domain='cluster.invalid', node_ip='192.0.2.10',
                          machine_cidr='192.0.2.0/24', subnet_id='subnet', vcn_id='vcn', resolver_id='resolver', view_id='synthetic-view')
            (Path(work)/'oci-dns-view.json').write_text(json.dumps(record))
            core = self.sdk.core.VirtualNetworkClient.return_value
            core.get_subnet.return_value.data = types.SimpleNamespace(compartment_id='synthetic-compartment',vcn_id='vcn',cidr_block='192.0.2.0/24',lifecycle_state='AVAILABLE')
            core.get_vcn.return_value.data = types.SimpleNamespace(id='vcn',compartment_id='synthetic-compartment')
            self.client.get_resolver.return_value.data = types.SimpleNamespace(compartment_id='synthetic-compartment',attached_vcn_id='vcn',default_view_id='synthetic-view')
            self.client.get_view.return_value.data = types.SimpleNamespace(id='synthetic-view',compartment_id='synthetic-compartment')
            self.sdk.pagination.list_call_get_all_results.return_value.data = []
            with patch.dict(sys.modules, {'oci':self.sdk}):
                dns.configure(*self.args, 'plan', '', '192.0.2.0/24')
            self.client.get_view.assert_called_with('synthetic-view')
            self.client.create_zone.assert_not_called()
            self.client.get_resolver.return_value.data.default_view_id = 'changed-view'
            with patch.dict(sys.modules, {'oci':self.sdk}), self.assertRaises(ValueError):
                dns.configure(*self.args, 'apply', '', '192.0.2.0/24')
            self.client.create_zone.assert_not_called()
            self.client.get_resolver.return_value.data.default_view_id = 'synthetic-view'
            self.client.create_zone.return_value.data = self.zone
            correct = [types.SimpleNamespace(domain=p+'cluster.invalid',rtype='A',rdata='192.0.2.10',ttl=60)
                       for p in ('api.','api-int.','*.apps.')]
            self.client.get_zone_records.side_effect = [
                types.SimpleNamespace(headers={'etag':'v1'},data=types.SimpleNamespace(items=[])),
                types.SimpleNamespace(headers={'etag':'v2'},data=types.SimpleNamespace(items=correct))]
            with patch.dict(sys.modules, {'oci':self.sdk}):
                dns.configure(*self.args, 'apply', '', '192.0.2.0/24')
            self.client.create_zone.assert_called_once()
            self.assertEqual(self.sdk.dns.models.CreateZoneDetails.call_args.kwargs['view_id'], 'synthetic-view')
            self.client.create_zone.reset_mock()
            record['region'] = 'wrong-region'
            (Path(work)/'oci-dns-view.json').write_text(json.dumps(record))
            with patch.dict(sys.modules, {'oci':self.sdk}), self.assertRaises(ValueError):
                dns.configure(*self.args, 'apply', '', '192.0.2.0/24')
            self.client.create_zone.assert_not_called()

    def test_apply_uses_etag_and_checks_readback(self):
        correct = [types.SimpleNamespace(domain=p+'cluster.invalid',rtype='A',rdata='192.0.2.10',ttl=60)
                   for p in ('api.','api-int.','*.apps.')]
        self.client.get_zone_records.side_effect = [
            types.SimpleNamespace(headers={'etag':'version-1'},data=types.SimpleNamespace(items=[])),
            types.SimpleNamespace(headers={'etag':'version-2'},data=types.SimpleNamespace(items=correct))]
        self.run_dns('apply')
        self.assertEqual(self.client.patch_zone_records.call_args.kwargs['if_match'], 'version-1')
        self.assertEqual(len(self.client.get_zone_records.call_args_list), 2)
        self.assertTrue(all(c.kwargs['limit'] == 100 for c in self.client.get_zone_records.call_args_list))

class DnsErrorTests(unittest.TestCase):
    def test_create_denial_reports_creation_permission(self):
        class Denied(Exception):
            status = 404
            code = 'NotAuthorizedOrNotFound'
            operation_name = 'create_zone'
            request_id = 'synthetic-request'
        import contextlib
        import io
        sdk = MagicMock()
        sdk.exceptions.ServiceError = Denied
        output = io.StringIO()
        with patch.dict(sys.modules, {'oci':sdk}), patch.object(dns,'configure',side_effect=Denied()), contextlib.redirect_stderr(output):
            self.assertEqual(dns.main([]), 1)
        self.assertIn('DNS_ZONE_CREATE', output.getvalue())
        self.assertIn('DNS_VIEW_INSPECT', output.getvalue())
        self.assertIn('manage dns-zones', output.getvalue())
        self.assertNotIn('A denied ListZones', output.getvalue())

    def test_authorization_failure_is_actionable_without_traceback(self):
        class Denied(Exception):
            status = 404
            code = 'NotAuthorizedOrNotFound'
            operation_name = 'list_zones'
            request_id = 'synthetic-request'
        import contextlib
        import io
        sdk = MagicMock()
        sdk.exceptions.ServiceError = Denied
        output = io.StringIO()
        with patch.dict(sys.modules, {'oci':sdk}), patch.object(dns,'configure',side_effect=Denied()), contextlib.redirect_stderr(output):
            self.assertEqual(dns.main([]), 1)
        self.assertIn('inspect dns-zones', output.getvalue())
        self.assertIn('does not prove the zone is absent', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())

if __name__ == '__main__': unittest.main()
