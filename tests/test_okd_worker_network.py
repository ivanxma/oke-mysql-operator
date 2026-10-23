"""Worker NSG ownership, membership preservation and least-privilege rules."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as Obj
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('worker_network',Path(__file__).resolve().parents[1]/'openshift/community/lib/prepare-worker-network.py')
network=importlib.util.module_from_spec(spec);spec.loader.exec_module(network)


class WorkerNetworkTests(unittest.TestCase):
    def test_nsg_discovery_uses_keyword_compartment_filter_required_by_sdk(self):
        def listing(*,compartment_id,vcn_id):
            self.assertEqual(compartment_id,'cid');self.assertEqual(vcn_id,'vcn')
            return Obj(data=['owned-group'])
        result=network.list_groups(Obj(list_network_security_groups=listing),lambda call,**kwargs:call(**kwargs),'cid','vcn')
        self.assertEqual(result,['owned-group'])

    def test_other_compartment_vcn_or_cluster_owned_group_is_refused(self):
        for cid,vcn,uid in [('other','vcn','uid'),('cid','other','uid'),('cid','vcn','other')]:
            with self.subTest(cid=cid,vcn=vcn,uid=uid),self.assertRaises(ValueError):
                network.owned(Obj(compartment_id=cid,vcn_id=vcn,freeform_tags={'okd_cluster_uid':uid}),'cid','vcn','uid')

    def test_vnic_update_preserves_existing_memberships_and_is_idempotent(self):
        vnic=Obj(compartment_id='cid',subnet_id='subnet',private_ip='192.0.2.20',public_ip=None,nsg_ids=['existing'])
        self.assertEqual(network.memberships(vnic,'cid','subnet','192.0.2.20','nodes'),['existing','nodes'])
        vnic.nsg_ids=['existing','nodes']
        self.assertEqual(network.memberships(vnic,'cid','subnet','192.0.2.20','nodes'),['existing','nodes'])
        vnic.private_ip='192.0.2.21'
        with self.assertRaises(ValueError):network.memberships(vnic,'cid','subnet','192.0.2.20','nodes')

    def test_membership_limit_is_not_resolved_by_removing_existing_groups(self):
        vnic=Obj(compartment_id='cid',subnet_id='subnet',private_ip='192.0.2.20',public_ip=None,nsg_ids=['a','b','c','d','e'])
        with self.assertRaises(ValueError):network.memberships(vnic,'cid','subnet','192.0.2.20','new')
        self.assertEqual(vnic.nsg_ids,['a','b','c','d','e'])

    def test_rules_are_self_group_udp_and_require_exact_port_stateful_match(self):
        for desired in network.rules('nodes'):
            self.assertEqual(desired['protocol'],'17')
            self.assertNotIn('0.0.0.0/0',desired.values())
            rule=Obj(**desired,udp_options=Obj(source_port_range=None,destination_port_range=Obj(min=6081,max=6081)))
            self.assertTrue(network.matches(rule,desired))
            rule.udp_options.destination_port_range.max=6082
            self.assertFalse(network.matches(rule,desired))
            rule.udp_options.destination_port_range.max=6081;rule.is_stateless=True
            self.assertFalse(network.matches(rule,desired))

    def test_receipt_is_private_and_symlink_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'state/worker-network.json';network.write(p,{'nsg_id':'synthetic'})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            p.unlink();outside=Path(temp)/'outside';outside.write_text('preserve');p.symlink_to(outside)
            with self.assertRaises(ValueError):network.write(p,{'changed':True})
            self.assertEqual(outside.read_text(),'preserve')


if __name__=='__main__':unittest.main()
