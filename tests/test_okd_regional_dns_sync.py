"""DNS replication preserves records and refuses wrong-scope private zones."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('dns_sync',Path(__file__).resolve().parents[1]/'openshift/community/lib/sync-regional-dns.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)


class RegionalDNSTests(unittest.TestCase):
    def zone(self,**kw):return Obj(**dict(dict(id='zone',compartment_id='cid',view_id='view',scope='PRIVATE',zone_type='PRIMARY',name='cluster.test'),**kw))

    def test_wrong_compartment_view_scope_and_name_refused(self):
        for change in [dict(compartment_id='other'),dict(view_id='other'),dict(scope='GLOBAL'),dict(name='other.test')]:
            with self.subTest(change=change),self.assertRaises(ValueError):sync.checked_zone(self.zone(**change),'cid','view','cluster.test')

    def test_preview_of_missing_replica_never_creates_zone(self):
        dns=Mock();oci=Obj(pagination=Obj(list_call_get_all_results=lambda *a,**k:Obj(data=[])))
        sync.synchronize({'domain':'cluster.test','NODE_IP':'192.0.2.20'},dict(dns=dns,OCI_COMPARTMENT_ID='cid',view='view',OCI_REGION='region-test'),'preview',oci,Mock())
        dns.create_zone.assert_not_called();dns.patch_zone_records.assert_not_called()

    def test_patch_uses_etag_and_existing_zone_without_replacement(self):
        dns=Mock();dns.get_zone.return_value=Obj(data=self.zone())
        dns.get_zone_records.return_value=Obj(headers={'etag':'version-1'},data=Obj(items=[]))
        oci=Obj(pagination=Obj(list_call_get_all_results=lambda *a,**k:Obj(data=[self.zone()])),dns=Obj(models=Obj(RecordOperation=lambda **k:k,PatchZoneRecordsDetails=lambda **k:k)))
        plan=Mock(side_effect=[[dict(operation='ADD',domain='api.cluster.test',rtype='A',rdata='192.0.2.20',ttl=60)],[]])
        sync.synchronize({'domain':'cluster.test','NODE_IP':'192.0.2.20'},dict(dns=dns,OCI_COMPARTMENT_ID='cid',view='view',OCI_REGION='region-test'),'apply',oci,plan)
        self.assertEqual(dns.patch_zone_records.call_args.kwargs['if_match'],'version-1')
        dns.create_zone.assert_not_called();dns.delete_zone.assert_not_called()


if __name__=='__main__':unittest.main()
