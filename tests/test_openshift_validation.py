"""Security-rule evaluation tests use documentation-only addresses."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'openshift/community/lib/validate-host.py'
spec = importlib.util.spec_from_file_location('okd_preflight', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SecurityRuleTests(unittest.TestCase):
    def rule(self, **updates):
        result = {'direction': 'INGRESS', 'protocol': '6', 'source-type': 'CIDR_BLOCK',
                  'source': '192.0.2.0/24', 'is-stateless': False,
                  'tcp-options': {'destination-port-range': {'min': 6443, 'max': 6443}}}
        result.update(updates)
        return result

    def test_stateful_matching_cidr_and_api_port(self):
        self.assertIs(module.rule_matches(self.rule(), 'INGRESS', 6, 6443, '192.0.2.20'), True)

    def test_wrong_source_port_protocol_and_direction_are_denied(self):
        for direction, protocol, port, address in (
            ('INGRESS', 6, 443, '192.0.2.20'), ('INGRESS', 17, 6443, '192.0.2.20'),
            ('INGRESS', 6, 6443, '198.51.100.20'), ('EGRESS', 6, 6443, '192.0.2.20')):
            with self.subTest(direction=direction, protocol=protocol, port=port):
                self.assertIs(module.rule_matches(self.rule(), direction, protocol, port, address), False)

    def test_stateless_and_restricted_source_ports_are_unproven(self):
        self.assertIsNone(module.rule_matches(self.rule(**{'is-stateless': True}), 'INGRESS', 6, 6443, '192.0.2.20'))
        rule = self.rule(**{'tcp-options': {'source-port-range': {'min': 1024, 'max': 2048}}})
        self.assertIsNone(module.rule_matches(rule, 'INGRESS', 6, 6443, '192.0.2.20'))

    def test_nsg_membership_is_required(self):
        rule = self.rule(**{'source-type': 'NETWORK_SECURITY_GROUP', 'source': 'example-source-group'})
        self.assertFalse(module.rule_matches(rule, 'INGRESS', 6, 6443, '192.0.2.20'))
        self.assertTrue(module.rule_matches(rule, 'INGRESS', 6, 6443, '192.0.2.20', ['example-source-group']))

    def test_all_protocol_and_egress_rules(self):
        rule = {'direction': 'EGRESS', 'protocol': 'all', 'destination': '0.0.0.0/0'}
        self.assertIs(module.rule_matches(rule, 'EGRESS', 6, 443, '192.0.2.20'), True)
        self.assertIs(module.rule_matches(rule, 'INGRESS', 6, 443, '192.0.2.20'), False)

    def test_invalid_cidr_does_not_grant_access(self):
        self.assertFalse(module.ip_in('192.0.2.20', 'not-a-cidr'))


if __name__ == '__main__':
    unittest.main()
