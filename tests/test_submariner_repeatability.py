"""Repeatability checks fail for stale evidence, wrong peers and incomplete rollout."""
import datetime
import importlib.util
import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
LIB=Path(__file__).resolve().parents[1]/'openshift/community/networking/submariner/lib'
sys.path.insert(0,str(LIB))
def module(name,file):
 s=importlib.util.spec_from_file_location(name,LIB/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
m=module('mesh_readiness','mesh-readiness.py');p=module('gateway_paths','verify-gateway-paths.py')
sys.path.pop(0)

class RepeatabilityTests(unittest.TestCase):
 def test_connected_public_or_wrong_peer_is_not_ready(self):
  remote={'status':{'localEndpoint':{'private_ip':'192.0.2.20'}}}
  g={'status':{'haStatus':'active','connections':[dict(status='connected',usingIP='192.0.2.20',endpoint=dict(cluster_id='peer'))]}}
  self.assertTrue(m.private_connection(g,remote,'peer'))
  g['status']['connections'][0]['usingIP']='198.51.100.20'
  self.assertFalse(m.private_connection(g,remote,'peer'))
  g['status']['connections'][0]['usingIP']='192.0.2.20'
  self.assertFalse(m.private_connection(g,remote,'different'))
  g['status']['haStatus']='passive'
  self.assertFalse(m.private_connection(g,remote,'peer'))
 def test_partial_rollout_cannot_pass(self):
  d={'items':[{'spec':{'replicas':2},'status':{'readyReplicas':2,'updatedReplicas':1}}]}
  ds={'items':[{'status':dict(desiredNumberScheduled=2,numberReady=2,updatedNumberScheduled=2)}]}
  self.assertFalse(m.rollout_ready(d,ds));d['items'][0]['status']['updatedReplicas']=2
  self.assertTrue(m.rollout_ready(d,ds))
  ds['items'][0]['status']['numberReady']=1;self.assertFalse(m.rollout_ready(d,ds))
 def test_junit_stale_failure_and_skips_are_distinguished(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'verify.xml';start=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=5)
   path.write_text('<testsuites><testsuite><testcase/><testcase><skipped/></testcase></testsuite></testsuites>')
   self.assertEqual(m.junit_summary(path,start),dict(passed=1,failed=0,skipped=1))
   with self.assertRaisesRegex(ValueError,'Stale'):m.junit_summary(path,start+datetime.timedelta(minutes=1))
   path.write_text('<testsuites errors="1"><testsuite><testcase/></testsuite></testsuites>')
   with self.assertRaisesRegex(ValueError,'failures'):m.junit_summary(path,start)
 def test_readiness_timeout_never_attempts_join(self):
  w=Mock()
  with patch.object(m,'selected',return_value={'target':dict(uid='u',id='target',gateway='worker')}),patch.object(m,'snapshot',return_value=False),patch.object(m.time,'monotonic',side_effect=[0,0,2]):
   with self.assertRaisesRegex(ValueError,'timeout'):m.wait(w,1,1)
  w.sub.assert_not_called()
 def test_probe_403_means_reachable_but_requires_verified_tls_and_ping(self):
  good=dict(route_ok=True,ping_ok=True,broker_http=403,broker_tls_verified=True)
  p.check_result(good)
  for changes in [dict(ping_ok=False),dict(broker_tls_verified=False),dict(broker_http=503)]:
   with self.assertRaises(ValueError):p.check_result(dict(good,**changes))

class ReportCleanupTests(unittest.TestCase):
 def test_report_waits_for_terminating_namespace_without_deleting(self):
  with tempfile.TemporaryDirectory() as tmp:
   state=Path(tmp);info={r:dict(uid=r+'-uid',id=r,gateway=r+'-worker') for r in ['target','peer']}
   start=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=5)
   record=dict(run_id='run',started_at=start.isoformat(),clusters={r:dict(uid=i['uid'],id=i['id']) for r,i in info.items()},namespaces={r+'/e2e-tests-one':{} for r in info})
   (state/'verifier-run.json').write_text(json.dumps(record));(state/'verify.xml').write_text('<testsuites><testsuite><testcase/></testsuite></testsuites>')
   w=Mock(state=state);empty={'items':[]}
   w.read.side_effect=[{'items':[{'metadata':{'name':'e2e-tests-one'},'status':{'phase':'Terminating'}}]},empty,empty,empty,empty,empty,empty,empty]
   with patch.object(m,'selected',return_value=info),patch.object(m.time,'sleep') as sleep:
    m.report(w,10,1)
   sleep.assert_called_once();w.sub.assert_not_called()
   self.assertTrue(json.loads((state/'verification-check.json').read_text())['cleanup_verified'])

if __name__=='__main__':unittest.main()
