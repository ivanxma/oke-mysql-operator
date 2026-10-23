"""Regional lab manifests, password isolation and generated-folder creation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/'openshift/community/mysql-operator'

def environment():
 result=os.environ.copy()
 for line in (DIR/'config.env.example').read_text().splitlines():
  if line and not line.startswith('#'):k,v=line.split('=',1);result[k]=v
 result.update(MYSQL_STATE_DIR='/tmp/regional-mysql-test',MYSQLK8S_GENERATED_DIR='/tmp/regional-mysql-test/generated',MYSQL_SUBMARINER_ID='site-a',MYSQL_WORKER_NAMES='worker-a')
 return result

def execute(code,env):
 prefix='import importlib.util; s=importlib.util.spec_from_file_location("regional",'+repr(str(DIR/'lib/regional.py'))+'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
 return subprocess.run(['python3','-c',prefix+code],env=env,capture_output=True,text=True,check=True).stdout

class RegionalMySQLTests(unittest.TestCase):
 def test_manifest_advertises_exported_fqdn_and_excludes_control_plane(self):
  env=environment();d=json.loads(execute('import json; print(json.dumps(m.manifest()))',env))
  self.assertEqual(d['spec']['instances'],1)
  self.assertEqual(d['spec']['baseServerId'],1000)
  self.assertEqual(d['spec']['serviceFqdnTemplate'],'site-a.{service}.{namespace}.svc.clusterset.local')
  expr=d['spec']['podSpec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchExpressions']
  self.assertTrue(all(any(e['key']==role and e['operator']=='DoesNotExist' for e in expr) for role in ['node-role.kubernetes.io/master','node-role.kubernetes.io/control-plane']))
  self.assertFalse(d['spec']['datadirPermissions']['setRightsUsingInitContainer'])
  self.assertNotIn('rootPassword',json.dumps(d))
 def test_lab_password_exception_does_not_weaken_normal_policy(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'password';p.write_text('Sample1!\n');p.chmod(0o600);env=environment();env['MYSQL_ROOT_PASSWORD_FILE']=str(p)
   self.assertIn('accepted',execute('m.credentials(); print("accepted")',env))
   env['MYSQL_LAB_MODE']='false'
   with self.assertRaises(subprocess.CalledProcessError):execute('m.credentials()',env)
   env['MYSQL_LAB_MODE']='true';p.chmod(0o644)
   with self.assertRaises(subprocess.CalledProcessError):execute('m.credentials()',env)
 def test_security_and_operator_watch_only_local_database_namespace(self):
  env=environment();out=json.loads(execute('import json; print(json.dumps({"values":m.operator_values(),"security":m.rt.security_documents()}))',env))
  self.assertEqual(out['values']['deployment']['namespaces'],['mysql-cluster1'])
  values=json.dumps(out['values']);self.assertIn('node-role.kubernetes.io/control-plane',values)
 def test_generated_scripts_use_relative_checkout_and_region_state(self):
  s=(DIR/'lib/create-workspace.py').read_text()
  self.assertIn("ROOT/'generated/openshift'",s)
  self.assertIn('Generated regional MySQL launcher.',s)
  self.assertNotIn('/Users/',s)
  self.assertNotIn('internal_doc',s)

if __name__=='__main__':unittest.main()
