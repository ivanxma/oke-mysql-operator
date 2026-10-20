#!/usr/bin/env python3
"""Isolated kubeconfig generation and c2 wrapper checks; no OCI/Kubernetes calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
a=sys.argv[1:]
with open(os.environ['OKE2_TEST_TRACE'],'a') as f: f.write(json.dumps([pathlib.Path(sys.argv[0]).name,a,os.environ.get('KUBECONFIG')])+'\n')
if pathlib.Path(sys.argv[0]).name=='oci':
 if 'create-kubeconfig' in a:
  identifier=a[a.index('--cluster-id')+1]
  doc={'current-context':'generated','contexts':[{'name':'generated','context':{'cluster':'cluster','user':'user'}}],
       'users':[{'name':'user','user':{'exec':{'command':'oci','args':['ce','cluster','generate-token','--cluster-id',identifier]}}}],
       'clusters':[{'name':'cluster','cluster':{'server':'https://10.0.0.2:6443','certificate-authority-data':'test'}}]}
  pathlib.Path(a[a.index('--file')+1]).write_text(json.dumps(doc))
 else: print(os.environ.get('OKE2_TEST_STATE','ACTIVE'))
elif pathlib.Path(sys.argv[0]).name=='kubectl' and 'config' in a:
 path=pathlib.Path(a[a.index('--kubeconfig')+1]); d=json.loads(path.read_text())
 if 'current-context' in a: print(d['current-context'])
 elif 'rename-context' in a:
  old,new=a[-2:]; d['contexts'][0]['name']=new; d['current-context']=new; path.write_text(json.dumps(d))
 elif 'use-context' in a: d['current-context']=a[-1]; path.write_text(json.dumps(d))
 elif 'view' in a: print(json.dumps(d))
 elif 'get-contexts' in a:
  if d['contexts'][0]['name']=='c2': print('c2')
else: print('OK')
'''


class OKE2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for name in ('oci', 'kubectl', 'helm'):
            file = self.path / name
            file.write_text(MOCK)
            file.chmod(0o755)
        self.kubeconfig = self.path / 'config2'
        self.config = self.path / 'settings.env'
        self.trace = self.path / 'trace'
        self.config.write_text(f'C2_CLUSTER_ID=ocid1.cluster.oc1.region.second\nC2_KUBECONFIG={self.kubeconfig}\n')
        self.env = {**os.environ, 'PATH': str(self.path) + os.pathsep + os.environ['PATH'],
                    'OKE2_CONFIG': str(self.config), 'OKE2_TEST_TRACE': str(self.trace)}

    def run_script(self, script, *args, success=True):
        result = subprocess.run(['bash', str(ROOT / 'oke2' / script), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_syntax(self):
        for file in list((ROOT / 'oke2').glob('*.sh')) + list((ROOT / 'oke2/lib').glob('*.sh')):
            subprocess.run(['bash', '-n', str(file)], check=True)

    def test_creates_only_config2_with_context_c2(self):
        self.run_script('02-create-kubeconfig.sh')
        doc = json.loads(self.kubeconfig.read_text())
        self.assertEqual(doc['current-context'], 'c2')
        self.assertEqual(self.kubeconfig.stat().st_mode & 0o777, 0o600)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        creation = next(c[1] for c in calls if 'create-kubeconfig' in c[1])
        self.assertIn('--with-auth-context', creation)
        self.assertTrue(Path(creation[creation.index('--file') + 1]).is_relative_to(self.path))

    def test_refresh_backs_up_matching_config2(self):
        self.run_script('02-create-kubeconfig.sh')
        previous = self.kubeconfig.read_bytes()
        self.run_script('02-create-kubeconfig.sh')
        backups = list(self.path.glob('config2.backup.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), previous)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_creating_cluster_leaves_config_unchanged(self):
        self.env['OKE2_TEST_STATE'] = 'CREATING'
        self.run_script('02-create-kubeconfig.sh', success=False)
        self.assertFalse(self.kubeconfig.exists())

    def test_different_cluster_config2_not_overwritten(self):
        self.run_script('02-create-kubeconfig.sh')
        doc = json.loads(self.kubeconfig.read_text())
        doc['users'][0]['user']['exec']['args'][-1] = 'ocid1.cluster.oc1.region.other'
        self.kubeconfig.write_text(json.dumps(doc))
        before = self.kubeconfig.read_bytes()
        self.run_script('02-create-kubeconfig.sh', success=False)
        self.assertEqual(self.kubeconfig.read_bytes(), before)

    def test_primary_path_rejected_before_any_oci_call(self):
        self.config.write_text(f'C2_KUBECONFIG={Path.home() / ".kube/config"}\n')
        result = self.run_script('02-create-kubeconfig.sh', success=False)
        self.assertIn('separate file', result.stderr)
        self.assertFalse(self.trace.exists())

    def test_symlink_destination_rejected(self):
        self.kubeconfig.symlink_to(self.path / 'another-file')
        self.run_script('02-create-kubeconfig.sh', success=False)
        self.assertFalse(self.trace.exists())

    def test_wrappers_explicitly_select_config2(self):
        self.run_script('02-create-kubeconfig.sh')
        self.run_script('kubectl.sh', 'get', 'nodes')
        self.run_script('helm.sh', 'list', '-A')
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        for executable, flag in (('kubectl', '--context'), ('helm', '--kube-context')):
            call = next(c for c in reversed(calls) if c[0] == executable and flag in c[1])
            self.assertEqual(call[1][call[1].index('--kubeconfig') + 1], str(self.kubeconfig))
            self.assertEqual(call[1][call[1].index(flag) + 1], 'c2')
            self.assertEqual(call[2], str(self.kubeconfig))

    def test_wrapper_context_override_rejected(self):
        self.run_script('02-create-kubeconfig.sh')
        self.run_script('kubectl.sh', '--context', 'first', 'get', 'nodes', success=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
