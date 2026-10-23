"""DNS acceptance parsing and polling without SSH, DNS or OCI access."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dns_verify', Path(__file__).resolve().parents[1]/'openshift/community/lib/verify-dns.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
GOOD = ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\nAPI.CLUSTER.INVALID. 60 IN A 192.0.2.20\n'
OS = '192.0.2.20 STREAM api.cluster.invalid\n192.0.2.20 DGRAM\n'

class ParsingTests(unittest.TestCase):
    def check(self, os=OS, dig=GOOD):
        return verify.interpret('api.cluster.invalid','192.0.2.20',os,dig)

    def test_expected_ip_passes(self):
        self.assertTrue(self.check()['passed'])

    def test_wrong_ip_fails(self):
        self.assertFalse(self.check(os='192.0.2.21 STREAM',dig=GOOD.replace('192.0.2.20','192.0.2.21'))['passed'])

    def test_hosts_entry_cannot_hide_dns_failure(self):
        self.assertFalse(self.check(dig=';; status: NXDOMAIN,')['passed'])

    def test_negative_cache_hint(self):
        result=self.check(os='',dig=';; status: NXDOMAIN,\n. 123 IN SOA ns.invalid. admin.invalid. 1 2 3 4 5')
        self.assertFalse(result['passed'])
        self.assertIn('negative-answer TTL 123s',result['detail'])

    def test_root_ns_response_does_not_pass(self):
        self.assertFalse(self.check(dig=';; status: NOERROR,\n. 100 IN NS root.invalid.')['passed'])

    def test_extra_address_fails(self):
        self.assertFalse(self.check(os=OS+'192.0.2.21 STREAM',dig=GOOD+'api.cluster.invalid. 60 IN A 192.0.2.21')['passed'])

class RunnerTests(unittest.TestCase):
    def report(self, role, passed=True):
        return dict(role=role,checks=[dict(name=n,passed=passed,detail='synthetic')
            for n in ('api.cluster.invalid','api-int.cluster.invalid','console.apps.cluster.invalid','probe-1.apps.cluster.invalid')])

    def run_check(self, report, wait='0'):
        with patch.object(verify.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'hostname 192.0.2.20\n','')), patch.object(verify,'remote_probe',side_effect=report):
            return verify.run('private-ssh-config','cluster.invalid','192.0.2.20',wait,'1')

    def test_success_does_not_wait(self):
        with patch.object(verify.time,'sleep') as sleep:
            self.assertEqual(self.run_check(lambda c,d,e,r:self.report(r)),0)
            sleep.assert_not_called()

    def test_single_failure_returns_failure(self):
        self.assertEqual(self.run_check(lambda c,d,e,r:self.report(r,r=='ADMIN')),1)

    def test_unreachable_host_keeps_four_checks_unknown(self):
        def report(c,d,e,r):
            return self.report(r) if r == 'ADMIN' else dict(role=r,checks=[],error='SSH authentication denied (exit 255)')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.run_check(report),1)
        self.assertIn('4 PASS, 0 FAIL, 4 NOT CHECKED (8 expected); 1 probe error(s)',output.getvalue())
        self.assertIn('SSH authentication denied',output.getvalue())
        self.assertNotIn('READY for full',output.getvalue())

    def test_unreachable_host_does_not_hide_remaining_dns_failures(self):
        def report(c,d,e,r):
            if r == 'TARGET':
                return dict(role=r,checks=[],error='Remote probe exceeded 65s')
            result = self.report(r,False)
            result['checks'][-1]['passed'] = True
            return result
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.run_check(report),1)
        self.assertIn('1 PASS, 3 FAIL, 4 NOT CHECKED (8 expected)',output.getvalue())

    def test_stale_target_refused(self):
        with patch.object(verify.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'hostname 192.0.2.99\n','')), patch.object(verify,'remote_probe') as probe, self.assertRaises(ValueError):
            verify.run('config','cluster.invalid','192.0.2.20','0','1')
        probe.assert_not_called()

    def test_polling_deadline_returns_failure_without_sleep(self):
        with patch.object(verify.time,'monotonic',side_effect=[100,102]), patch.object(verify.time,'sleep') as sleep:
            self.assertEqual(self.run_check(lambda c,d,e,r:self.report(r,False),wait='1'),1)
            sleep.assert_not_called()

    def test_polling_can_recover(self):
        calls=[]
        def report(c,d,e,r):
            calls.append(r)
            return self.report(r, len(calls)>2)
        with patch.object(verify.time,'monotonic',side_effect=[100,100]), patch.object(verify.time,'sleep') as sleep:
            self.assertEqual(self.run_check(report,wait='5'),0)
            sleep.assert_called_once_with(1)

class RemoteProbeTests(unittest.TestCase):
    def remote(self, result):
        with patch.object(verify.subprocess,'run',return_value=result):
            return verify.remote_probe('config','cluster.invalid','192.0.2.20','TARGET')

    def test_authentication_failure_explained_without_disclosing_stderr(self):
        result = self.remote(subprocess.CompletedProcess([],255,'','Permission denied (publickey). /private/key-path'))
        self.assertEqual(result['checks'],[])
        self.assertIn('SSH authentication denied (exit 255)',result['error'])
        self.assertNotIn('/private/key-path',result['error'])

    def test_timeout_is_not_a_dns_failure(self):
        with patch.object(verify.subprocess,'run',side_effect=subprocess.TimeoutExpired('ssh',65)):
            result = verify.remote_probe('config','cluster.invalid','192.0.2.20','TARGET')
        self.assertEqual(result['checks'],[])
        self.assertIn('exceeded 65s',result['error'])

    def test_invalid_json_is_probe_error(self):
        result = self.remote(subprocess.CompletedProcess([],0,'not json',''))
        self.assertIn('invalid JSON',result['error'])

    def test_invalid_check_types_are_rejected(self):
        report = dict(role='TARGET', checks=[dict(name='x',passed='false',detail='x')]*4)
        result = self.remote(subprocess.CompletedProcess([],0,json.dumps(report),''))
        self.assertIn('malformed',result['error'])

    def test_other_cluster_response_is_rejected(self):
        report = dict(role='TARGET', checks=[dict(name='other.invalid',passed=True,detail='x')]*4)
        result = self.remote(subprocess.CompletedProcess([],0,json.dumps(report),''))
        self.assertIn('selected cluster',result['error'])

    def test_valid_complete_report_is_accepted(self):
        names = ['api.cluster.invalid','api-int.cluster.invalid',
                 'console-openshift-console.apps.cluster.invalid','probe-123.apps.cluster.invalid']
        report = dict(role='TARGET',checks=[dict(name=n,passed=True,detail='192.0.2.20') for n in names])
        self.assertEqual(self.remote(subprocess.CompletedProcess([],0,json.dumps(report),'')),report)

if __name__ == '__main__': unittest.main()
