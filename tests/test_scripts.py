#!/usr/bin/env python3
"""Isolated behavioral checks; no real Kubernetes, Helm, or package operations."""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''#!/usr/bin/env python3
import base64,json,os,sys
a=sys.argv[1:]
with open(os.environ['TEST_TRACE'],'a') as f: f.write(json.dumps([os.path.basename(sys.argv[0])]+a)+'\n')
mode=os.environ.get('TEST_MODE','')
def out(v): print(json.dumps(v))
if os.path.basename(sys.argv[0]) == 'helm':
    if 'list' in a:
        if '--all' in a:
            sys.exit('Error: unknown flag: --all')
        out([{'chart':'mysql-operator-2.3.0','status':'failed' if mode=='failed-release' else 'deployed'}])
    sys.exit(0)
if 'current-context' in a: print('test-cluster')
elif 'can-i' in a: print('yes')
elif 'storageclasses' in a:
    out({'items': [] if mode=='no-storage' else [{'metadata':{'name':'test-csi','annotations':{'storageclass.kubernetes.io/is-default-class':'true'}}}]})
elif 'nodes' in a and 'json' in a:
    out({'items': [] if mode=='no-nodes' else [{'metadata':{'name':'worker-'+str(i),'labels':{'kubernetes.io/os':'linux','kubernetes.io/arch':'amd64'}},'status':{'conditions':[{'type':'Ready','status':'True'}]}} for i in range(3)]})
elif 'get' in a and 'namespace' in a: print('namespace/mysql')
elif 'get' in a and 'innodbcluster' in a and 'json' in a:
    out({'spec':{'instances':3},'status':{'cluster':{'status':'PENDING' if mode=='pending' else 'ONLINE','onlineInstances':0 if mode=='pending' else 3}}})
elif 'get' in a and 'innodbcluster' in a and '--ignore-not-found' in a:
    if mode not in ('new-secret','reuse-secret'): print('innodbcluster/mycluster')
elif 'get' in a and 'secret' in a:
    if mode not in ('missing-secret','new-secret'):
        if 'json' in a: out({'data':{k:base64.b64encode(v.encode()).decode() for k,v in {'rootUser':'root','rootHost':'%','rootPassword':'existing-password'}.items()}})
        else: print('secret/mycluster-root')
elif 'create' in a and 'secret' in a:
    for arg in a:
        if arg.startswith('--from-file=rootPassword='):
            assert open(arg.split('=',2)[2]).read() == 'test-password-1234'
    print('secret created')
'''


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        # Copy only required sources so generated files stay isolated.
        import shutil
        self.root = self.path / 'project'
        self.root.mkdir()
        shutil.copytree(ROOT / 'lib', self.root / 'lib')
        for p in ROOT.glob('0*.sh'):
            shutil.copy(p, self.root / p.name)
        for command in ('kubectl', 'helm'):
            script = self.path / command
            script.write_text(MOCK)
            script.chmod(0o755)
        self.trace = self.path / 'trace.jsonl'
        self.config = self.path / 'config.env'
        self.config.write_text('')
        password = self.path / 'password'
        password.write_text('test-password-1234\n')
        self.env = {**os.environ, 'PATH': str(self.path) + os.pathsep + os.environ['PATH'],
                    'MYSQLK8S_CONFIG': str(self.config), 'TEST_TRACE': str(self.trace),
                    'MYSQL_ROOT_PASSWORD_FILE': str(password), 'WAIT_TIMEOUT_SECONDS': '1'}

    def run_step(self, number, mode='', success=True):
        self.env['TEST_MODE'] = mode
        script = next(self.root.glob(f'{number:02}-*.sh'))
        result = subprocess.run(['bash', str(script)], env=self.env, capture_output=True, text=True, timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def test_shell_syntax(self):
        for script in list(ROOT.glob('[0-9][0-9]-*.sh')) + list((ROOT / 'lib').glob('*.sh')):
            subprocess.run(['bash', '-n', str(script)], check=True)

    def test_preflight_and_storage_selection(self):
        self.assertIn('Preflight passed', self.run_step(3).stdout)

    def test_missing_storage_fails(self):
        self.assertIn('STORAGE_CLASS', self.run_step(3, 'no-storage', False).stderr)

    def test_not_enough_workers_fails(self):
        self.assertIn('Need 3', self.run_step(3, 'no-nodes', False).stderr)

    def test_failed_operator_not_reinstalled(self):
        self.run_step(4, 'failed-release', False)
        self.assertFalse(any(c[0] == 'helm' and ('install' in c or 'upgrade' in c) for c in self.calls()))

    def test_existing_operator_reused(self):
        self.run_step(4)
        self.assertFalse(any(c[0] == 'helm' and 'install' in c for c in self.calls()))

    def test_release_status_filters_support_helm4(self):
        self.run_step(4)
        call = next(c for c in self.calls() if c[0] == 'helm' and 'list' in c)
        self.assertNotIn('--all', call)
        for flag in ('--deployed', '--failed', '--pending', '--uninstalled', '--superseded', '--uninstalling'):
            self.assertIn(flag, call)

    def test_new_secret_and_manifest(self):
        result = self.run_step(5, 'new-secret')
        data = json.loads((self.root / 'generated/innodbcluster.json').read_text())
        self.assertEqual(data['spec']['datadirVolumeClaimTemplate']['storageClassName'], 'test-csi')
        self.assertEqual(data['spec']['instances'], 3)
        self.assertIn('requiredDuringSchedulingIgnoredDuringExecution', data['spec']['podSpec']['affinity']['podAntiAffinity'])
        for c in self.calls():
            self.assertNotIn('test-password-1234', json.dumps(c))
        self.assertNotIn('test-password-1234', result.stdout + result.stderr)
        password_args = [a for c in self.calls() for a in c if a.startswith('--from-file=rootPassword=')]
        self.assertEqual(len(password_args), 1)
        self.assertFalse(Path(password_args[0].split('=', 2)[2]).exists())

    def test_existing_secret_reused(self):
        self.run_step(5, 'reuse-secret')
        self.assertFalse(any('create' in c and 'secret' in c for c in self.calls()))

    def test_missing_original_secret_stops(self):
        result = self.run_step(5, 'missing-secret', False)
        self.assertIn('Restore the original Secret', result.stderr)
        self.assertFalse(any('create' in c and 'secret' in c for c in self.calls()))

    def test_online_and_router_rollout(self):
        self.run_step(6)
        self.assertTrue(any('rollout' in c and 'deployment/mycluster-router' in c for c in self.calls()))

    def test_timeout_diagnostics(self):
        result = self.run_step(6, 'pending', False)
        self.assertIn('Timed out', result.stderr)
        self.assertTrue(any('events' in c for c in self.calls()))

    def test_context_pinned(self):
        self.config.write_text('KUBE_CONTEXT=explicit-cluster\n')
        self.run_step(4)
        for call in self.calls():
            if call[0] == 'kubectl':
                self.assertEqual(call[call.index('--context') + 1], 'explicit-cluster')
            else:
                self.assertEqual(call[call.index('--kube-context') + 1], 'explicit-cluster')


if __name__ == '__main__':
    unittest.main(verbosity=2)
