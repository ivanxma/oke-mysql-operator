"""Fingerprint-pinned SSH identity recovery; real key tools, no network access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('key_recovery', ROOT/'openshift/community/lib/refresh-target-host-key.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='okd key recovery ')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.config = self.state/'ssh_config'
        self.config.write_text('synthetic SSH settings')
        self.known = self.state/'known_hosts'
        keys = []
        for name in ('old','new'):
            key = self.state/name
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            keys.append(' '.join(key.with_suffix('.pub').read_text().split()[:2]))
        self.old, self.new = keys
        self.original = '192.0.2.20 '+self.old+'\n192.0.2.10 '+self.old+'\n'
        self.known.write_text(self.original)
        self.known.chmod(0o600)
        self.fingerprint = subprocess.run(['ssh-keygen','-lf',str(self.state/'new.pub')],
                                          capture_output=True,text=True,check=True).stdout.split()[1]
        self.calls = []
        self.fail_auth = False
        self.wrong_target = False

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == 'ssh-keygen':
            return subprocess.run(args,capture_output=True,text=True,**kwargs)
        if '-G' in args:
            return subprocess.CompletedProcess(args,0,
                'hostname '+('192.0.2.99' if self.wrong_target else '192.0.2.20')+
                '\nport 22\nproxyjump okd-admin\nuserknownhostsfile '+shlex.quote(str(self.known))+'\n','')
        if args[-2] == 'okd-admin':
            self.assertIn('StrictHostKeyChecking=yes',args)
            return subprocess.CompletedProcess(args,0,'192.0.2.20 '+self.new+'\n','')
        self.assertEqual(args[-2:],['okd-target','true'])
        self.assertIn('StrictHostKeyChecking=yes',args)
        self.assertIn('HostKeyAlgorithms=ssh-ed25519',args)
        option = next(x.split('=',1)[1] for x in args if x.startswith('UserKnownHostsFile='))
        candidate = Path(shlex.split(option)[0])
        self.assertEqual(candidate.read_text(),'192.0.2.20 '+self.new+'\n')
        return subprocess.CompletedProcess(args,255 if self.fail_auth else 0,'','')

    def run_recovery(self, action='preview', fingerprint=None):
        with patch.object(recovery,'command',side_effect=self.command), contextlib.redirect_stdout(io.StringIO()):
            recovery.refresh(str(self.state),'192.0.2.20',fingerprint or self.fingerprint,action)

    def test_preview_verifies_but_preserves_saved_keys(self):
        self.run_recovery()
        self.assertEqual(self.known.read_text(),self.original)
        self.assertEqual(list(self.state.glob('known_hosts.backup-*')),[])
        self.assertFalse(list(self.state.glob('.host-key-check-*')))

    def test_apply_changes_only_target_and_backs_up(self):
        self.run_recovery('apply')
        content = self.known.read_text()
        self.assertIn('192.0.2.10 '+self.old,content)
        self.assertIn('192.0.2.20 '+self.new,content)
        self.assertNotIn('192.0.2.20 '+self.old,content)
        backups = list(self.state.glob('known_hosts.backup-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_text(),self.original)
        self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        self.assertEqual(self.known.stat().st_mode & 0o777,0o600)

    def test_fingerprint_mismatch_never_replaces_keys(self):
        with self.assertRaisesRegex(ValueError,'DOES NOT MATCH'):
            self.run_recovery('apply','SHA256:'+'A'*43)
        self.assertEqual(self.known.read_text(),self.original)
        self.assertFalse(any(args[-1] == 'true' for args in self.calls))

    def test_authentication_failure_preserves_keys(self):
        self.fail_auth = True
        with self.assertRaisesRegex(ValueError,'authentication failed'):
            self.run_recovery('apply')
        self.assertEqual(self.known.read_text(),self.original)

    def test_stale_target_is_refused_before_scan(self):
        self.wrong_target = True
        with self.assertRaisesRegex(ValueError,'differs'):
            self.run_recovery('apply')
        self.assertEqual(len(self.calls),1)

    def test_symlink_known_hosts_refused(self):
        self.known.rename(self.state/'original')
        self.known.symlink_to(self.state/'original')
        with self.assertRaisesRegex(ValueError,'symlink'):
            self.run_recovery('apply')
        self.assertEqual(self.calls,[])

    def test_invalid_fingerprint_refused_before_network(self):
        with self.assertRaisesRegex(ValueError,'fingerprint'):
            self.run_recovery('apply','not-a-fingerprint')
        self.assertEqual(self.calls,[])

    def test_hashed_target_record_replaced(self):
        subprocess.run(['ssh-keygen','-H','-f',str(self.known)],capture_output=True,check=True)
        self.run_recovery('apply')
        found = subprocess.run(['ssh-keygen','-F','192.0.2.20','-f',str(self.known)],
                               capture_output=True,text=True,check=True).stdout
        self.assertIn(self.new,found)
        self.assertNotIn(self.old,found)


if __name__ == '__main__':
    unittest.main()
