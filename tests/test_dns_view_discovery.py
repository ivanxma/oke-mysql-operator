"""Read-only DNS view discovery with compartment and ambiguity guards."""
import importlib.util
from pathlib import Path
import sys
import types
import tempfile
import json
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('view_discovery', Path(__file__).resolve().parents[1]/'openshift/community/lib/discover-dns-view.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
S = types.SimpleNamespace

class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = self.tmp.name
        self.sdk = MagicMock()
        self.sdk.identity.IdentityClient.return_value.get_compartment.return_value.data = S(id='allowed',name='Synthetic',lifecycle_state='ACTIVE')
        self.subnet = S(id='subnet',vcn_id='vcn',compartment_id='allowed',lifecycle_state='AVAILABLE',cidr_block='192.0.2.0/24')
        self.resolver = S(id='resolver',compartment_id='allowed',attached_vcn_id='vcn',default_view_id='view')
        self.sdk.pagination.list_call_get_all_results.side_effect = [S(data=[self.subnet]),S(data=[self.resolver])]
        self.core = self.sdk.core.VirtualNetworkClient.return_value
        self.core.get_vcn.return_value.data = S(id='vcn',compartment_id='allowed')
        self.dns = self.sdk.dns.DnsClient.return_value
        self.dns.get_resolver.return_value.data = self.resolver
        self.dns.get_view.return_value.data = S(id='view',compartment_id='allowed')

    def discover(self):
        with patch.dict(sys.modules, {'oci':self.sdk}):
            return module.discover(self.work,'allowed','Synthetic','synthetic-region','192.0.2.0/24','192.0.2.20','cluster.invalid')

    def test_correct_mapping_and_read_only(self):
        self.assertEqual(self.discover(),'view')
        receipt = Path(self.work)/'oci-dns-view.json'
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(receipt.read_text())['cluster_domain'], 'cluster.invalid')
        for client in (self.core,self.dns):
            self.assertTrue(all(call[0].split('.')[0].startswith(('get_','list_')) for call in client.mock_calls))

    def test_foreign_saved_state_refused_before_cloud(self):
        (Path(self.work)/'oci-dns-view.json').write_text(json.dumps({'region':'other'}))
        with self.assertRaises(ValueError): self.discover()
        self.sdk.identity.IdentityClient.assert_not_called()

    def test_ambiguous_subnets_refused(self):
        self.sdk.pagination.list_call_get_all_results.side_effect = [S(data=[self.subnet,self.subnet])]
        with self.assertRaises(ValueError): self.discover()
        self.core.get_vcn.assert_not_called()

    def test_foreign_vcn_refused(self):
        self.core.get_vcn.return_value.data.compartment_id = 'other'
        with self.assertRaises(ValueError): self.discover()
        self.dns.get_resolver.assert_not_called()

    def test_foreign_view_refused(self):
        self.dns.get_view.return_value.data.compartment_id = 'other'
        with self.assertRaises(ValueError): self.discover()

    def test_wrong_registered_ip_refused(self):
        with patch.dict(sys.modules, {'oci':self.sdk}), self.assertRaises(ValueError):
            module.discover(self.work,'allowed','Synthetic','synthetic-region','192.0.2.0/24','198.51.100.20','cluster.invalid')
        self.sdk.identity.IdentityClient.assert_not_called()

if __name__ == '__main__': unittest.main()
