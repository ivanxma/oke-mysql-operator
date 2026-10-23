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
 def test_existing_csi_scope_must_match_before_database_creation(self):
  env=environment();env.update(MYSQL_OCI_COMPARTMENT_ID='selected',MYSQL_OCI_REGION='region-a')
  code='import base64,json; sc=dict(provisioner="blockvolume.csi.oraclecloud.com",reclaimPolicy="Retain",volumeBindingMode="WaitForFirstConsumer",parameters={"attachment-type":"paravirtualized"}); secret={"data":{"config.yaml":base64.b64encode(json.dumps(dict(useInstancePrincipals=True,compartment="selected",region="region-a")).encode()).decode()}}; m.check_storage_scope(sc,secret); print("scope accepted")'
  self.assertIn('scope accepted',execute(code,env))
  env['MYSQL_OCI_COMPARTMENT_ID']='other'
  with self.assertRaises(subprocess.CalledProcessError):execute(code,env)

 def test_recovery_preview_does_not_run_actual_reboot(self):
  env=environment();env.update(MYSQL_SITE_ROLE='PRIMARY',MYSQL_NAMESPACE='mysql-cluster1',MYSQL_CLUSTER_NAME='cluster1',MYSQL_INSTANCES='1')
  code='calls=[]; m.get=lambda *a,**k: {"spec":{"instances":1}}; m.sql=lambda code: calls.append(code) or ([["OFFLINE"]] if "SELECT MEMBER_STATE" in code else {}); m.recover_primary(); assert len(calls)==2; assert "dryRun" in calls[-1]; assert "force" not in "".join(calls); print("preview safe")'
  self.assertIn('preview safe',execute(code,env))
  env['MYSQL_SITE_ROLE']='REPLICA'
  with self.assertRaises(subprocess.CalledProcessError):execute(code,env)
 def test_recovery_refuses_ambiguous_membership(self):
  env=environment();env.update(MYSQL_SITE_ROLE='PRIMARY',MYSQL_INSTANCES='1')
  code='m.get=lambda *a,**k: {"spec":{"instances":1}}; m.sql=lambda code: [["RECOVERING"]]; m.recover_primary(True)'
  with self.assertRaises(subprocess.CalledProcessError):execute(code,env)
 def test_exports_prepare_peer_namespace_without_remote_database(self):
  env=environment();env.update(MYSQL_NAMESPACE='mysql-cluster1',MYSQL_CLUSTER_NAME='cluster1')
  code='import json; docs=[]; m.peer_env=lambda: {"MYSQL_NAMESPACE":"mysql-cluster2"}; m.peer_get=lambda *a: {}; m.rt.managed=lambda *a: None; m.own_apply=docs.append; m.export_service(); assert docs[0]["kind"]=="Namespace" and docs[0]["metadata"]["name"]=="mysql-cluster2"; assert [d["kind"] for d in docs]==["Namespace","ServiceExport"]; print("namespace safe")'
  self.assertIn('namespace safe',execute(code,env))

 def test_generated_scripts_use_relative_checkout_and_region_state(self):
  s=(DIR/'lib/create-workspace.py').read_text()
  self.assertIn("ROOT/'generated/openshift'",s)
  self.assertIn('Generated regional MySQL launcher.',s)
  self.assertNotIn('/Users/',s)
  self.assertNotIn('internal_doc',s)

if __name__=='__main__':unittest.main()
