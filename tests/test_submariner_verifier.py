"""Verifier admission grants stay inside fresh, upstream-labelled test namespaces."""
import datetime
import importlib.util
from pathlib import Path
import sys
import unittest
import json
import tempfile
from unittest.mock import Mock,patch
from types import SimpleNamespace as N
LIB=Path(__file__).resolve().parents[1]/'openshift/community/networking/submariner/lib'
sys.path.insert(0,str(LIB))
s=importlib.util.spec_from_file_location('verifier_security',LIB/'verifier.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
sys.path.pop(0)
class VerifierTests(unittest.TestCase):
 def test_existing_or_application_namespace_never_receives_grant(self):
  now=datetime.datetime.now(datetime.timezone.utc)
  ns={'metadata':dict(name='e2e-tests-discovery-abc',uid='ns-uid',creationTimestamp=now.isoformat(),labels={'pod-security.kubernetes.io/enforce':'privileged','security.openshift.io/scc.podSecurityLabelSync':'false'})}
  self.assertEqual(m.checked_namespace(ns,'e2e-tests-discovery-abc',now),'ns-uid')
  for name in ['production','default','e2e-tests-other']:
   with self.assertRaises(ValueError):m.checked_namespace(ns,name,now)
  with self.assertRaises(ValueError):m.checked_namespace(ns,'e2e-tests-discovery-abc',now+datetime.timedelta(minutes=1))
  ns['metadata']['labels']={}
  with self.assertRaises(ValueError):m.checked_namespace(ns,'e2e-tests-discovery-abc',now)
 def test_binding_is_namespaced_nonroot_default_sa_with_owner(self):
  b=m.binding('e2e-tests-discovery-abc','ns-uid','run-id')
  self.assertEqual(b['kind'],'RoleBinding')
  self.assertEqual(b['roleRef']['name'],'system:openshift:scc:nonroot-v2')
  self.assertEqual(b['subjects'],[dict(kind='ServiceAccount',name='default',namespace='e2e-tests-discovery-abc')])
  self.assertEqual(b['metadata']['ownerReferences'][0]['uid'],'ns-uid')
 def test_native_stream_grants_only_fresh_namespace_and_cleans_owned_binding(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=N(state=Path(tmp),version='0.24.2',bin=Path(tmp)/'subctl',env={'SUBMARINER_TARGET_ID':'target','SUBMARINER_PEER_ID':'peer'})
   w.selection=lambda peer: (['oc','--kubeconfig','peer' if peer else 'target'],Path(tmp)/('peer' if peer else 'target'),'same-context')
   role={'metadata':{'name':m.ROLE},'rules':[dict(apiGroups=['security.openshift.io'],resources=['securitycontextconstraints'],resourceNames=['nonroot-v2'],verbs=['use'])]}
   w.read=Mock(side_effect=[role,role])
   now=datetime.datetime.now(datetime.timezone.utc)
   ns={'metadata':dict(name='e2e-tests-discovery-abc',uid='ns-uid',creationTimestamp=now.isoformat(),labels={'pod-security.kubernetes.io/enforce':'privileged','security.openshift.io/scc.podSecurityLabelSync':'false'})}
   child=Mock(stdout=iter(['Generated namespace "e2e-tests-discovery-abc" in cluster "target" to execute tests in\n']))
   child.wait.return_value=0;child.poll.return_value=0
   with patch.object(m.subprocess,'Popen',return_value=child),patch.object(m.subprocess,'run',side_effect=[N(returncode=0,stdout=json.dumps(ns)),N(returncode=0,stdout=json.dumps(ns)),N(returncode=0,stdout=json.dumps({'metadata':{'uid':'binding-uid'}})),N(returncode=0,stdout='')]) as calls,patch.object(m,'run',side_effect=['v0.24.2','']) as invoke:
    m.verify(w)
   create=calls.call_args_list[2]
   self.assertIn('create',create.args[0]);self.assertEqual(json.loads(create.kwargs['input'])['roleRef']['name'],m.ROLE)
   self.assertEqual(json.loads((Path(tmp)/'verifier-run.json').read_text())['namespaces']['target/e2e-tests-discovery-abc']['rolebinding_uid'],'binding-uid')
   self.assertIn('rolebinding',calls.call_args_list[-1].args[0])

 def test_completed_namespace_does_not_abort_native_suite_or_receive_grant(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=N(state=Path(tmp),version='0.24.2',bin=Path(tmp)/'subctl',env={'SUBMARINER_TARGET_ID':'target','SUBMARINER_PEER_ID':'peer'})
   w.selection=lambda peer: (['oc'],Path(tmp)/('peer' if peer else 'target'),'context')
   role={'metadata':{'name':m.ROLE},'rules':[dict(apiGroups=['security.openshift.io'],resources=['securitycontextconstraints'],resourceNames=['nonroot-v2'],verbs=['use'])]}
   w.read=Mock(side_effect=[role,role]);now=datetime.datetime.now(datetime.timezone.utc).isoformat()
   ns={'metadata':dict(name='e2e-tests-dataplane-status-abc',uid='ns-uid',creationTimestamp=now,deletionTimestamp=now,labels={'pod-security.kubernetes.io/enforce':'privileged','security.openshift.io/scc.podSecurityLabelSync':'false'}),'status':{'phase':'Terminating'}}
   child=Mock(stdout=iter(['Generated namespace "e2e-tests-dataplane-status-abc" in cluster "target" to execute tests in\n']));child.wait.return_value=0;child.poll.return_value=0
   with patch.object(m.subprocess,'Popen',return_value=child),patch.object(m.subprocess,'run',return_value=N(returncode=0,stdout=json.dumps(ns))) as calls,patch.object(m,'run',return_value='v0.24.2') as invoke:
    m.verify(w)
   invoke.assert_called_once();self.assertEqual(calls.call_count,1)

if __name__=='__main__':unittest.main()
