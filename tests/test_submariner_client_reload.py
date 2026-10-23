"""A reload must authenticate with verified TLS and target a deployment-owned pod."""
import base64
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,MagicMock
DIR=Path(__file__).resolve().parents[1]/'openshift/community/networking/submariner/lib'
sys.path.insert(0,str(DIR))
spec=importlib.util.spec_from_file_location('reload_client',DIR/'restart-service-discovery.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Fake:
 env={'SUBMARINER_TARGET_API':'https://api.example.test:6443'}
 def selection(self,peer):return ['oc'],None,None
 def read(self,peer,*args):
  kind=args[1]
  if kind=='submariners.submariner.io':return {'items':[{'spec':{'brokerK8sApiServer':'api.example.test:6443','brokerK8sSecret':'submariner-broker-secret','brokerK8sRemoteNamespace':'broker'}}]}
  if kind=='secret':return {'data':{k:base64.b64encode(v.encode()).decode() for k,v in {'ca.crt':'test-ca','token':'test-token'}.items()}}
  if kind=='deployment':return {'metadata':{'uid':'deployment-uid','namespace':'submariner-operator'},'spec':{'replicas':1}}
  if kind=='replicasets':return {'items':[{'metadata':{'uid':'rs-uid','ownerReferences':[{'uid':'deployment-uid','controller':True}]}}]}
  if kind=='pods':return {'items':[{'metadata':{'uid':'pod-uid','ownerReferences':[{'uid':'rs-uid','controller':True}]}}]}
  raise AssertionError(kind)
class ReloadTests(unittest.TestCase):
 def test_plan_verifies_tls_token_and_pod_owner(self):
  response=MagicMock();response.__enter__.return_value.status=200
  with patch.object(m.mesh,'selected',return_value={'target':{'id':'site-a'}}),patch.object(m.ssl,'create_default_context',return_value='verified-context') as ctx,patch.object(m.urllib.request,'urlopen',return_value=response) as url:
   plans=m.plan(Fake());self.assertEqual(plans[0][2]['metadata']['uid'],'pod-uid')
   ctx.assert_called_once_with(cadata='test-ca');self.assertEqual(url.call_args.kwargs['context'],'verified-context')
   self.assertEqual(url.call_args.args[0].get_header('Authorization'),'Bearer test-token')
 def test_wrong_broker_cannot_trigger_a_reload(self):
  w=Fake();w.env={'SUBMARINER_TARGET_API':'https://other.example.test:6443'}
  with patch.object(m.mesh,'selected',return_value={'target':{'id':'site-a'}}),patch.object(m.urllib.request,'urlopen') as url:
   with self.assertRaises(ValueError):m.plan(w)
   url.assert_not_called()
if __name__=='__main__':unittest.main()
