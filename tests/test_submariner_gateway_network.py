"""Exact peer scope, NSG ownership and longest-prefix gateway routing."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as N
import unittest

LIB=Path(__file__).resolve().parents[1]/'openshift/community/networking/submariner/lib'
sys.path.insert(0,str(LIB))
spec=importlib.util.spec_from_file_location('gateway_network',LIB/'gateway-network.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sys.path.pop(0)

class GatewayNetworkTests(unittest.TestCase):
    def test_rules_only_exact_peer_and_required_protocols(self):
        r=m.peer_rules('192.0.2.20');self.assertEqual(len(r),6)
        for rule in r:
            field='source' if rule['direction']=='INGRESS' else 'destination'
            self.assertEqual(rule[field],'192.0.2.20/32');self.assertFalse(rule['is_stateless'])
            self.assertIn(rule['protocol'],('17','50','1'))
            if rule['protocol']=='17':self.assertEqual(rule['udp_options']['destination_port_range'],dict(min=4500,max=4500))
    def test_rule_reuse_rejects_other_scope_or_port(self):
        r=m.peer_rules('192.0.2.20')[0]
        self.assertTrue(m.matching(r,r))
        self.assertFalse(m.matching(dict(r,source='0.0.0.0/0'),r))
        self.assertFalse(m.matching(dict(r,udp_options=dict(destination_port_range=dict(min=4490,max=4500))),r))
    def test_narrow_nat_override_cannot_be_hidden_by_broad_drg(self):
        rules=[N(destination_type='CIDR_BLOCK',destination='192.0.2.0/24',network_entity_id='ocid1.drg.test')]
        m.route_to_peer(rules,'192.0.2.20')
        rules.append(N(destination_type='CIDR_BLOCK',destination='192.0.2.20/32',network_entity_id='ocid1.natgateway.test'))
        with self.assertRaisesRegex(ValueError,'DRG'):m.route_to_peer(rules,'192.0.2.20')
    def test_owned_group_requires_both_cluster_ids_and_compartment(self):
        g=N(compartment_id='cid',vcn_id='vcn',lifecycle_state='AVAILABLE',freeform_tags=dict(okd_cluster_uid='local',submariner_peer_uid='peer'))
        m.owned(g,'cid','vcn','local','peer')
        for args in [('other','vcn','local','peer'),('cid','vcn','local','wrong')]:
            with self.assertRaises(ValueError):m.owned(g,*args)

if __name__=='__main__':unittest.main()
