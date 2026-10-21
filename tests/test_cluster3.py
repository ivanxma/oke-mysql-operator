#!/usr/bin/env python3
"""Safety boundaries for cross-OKE DNS, credentials and native ClusterSet bootstrap."""
import base64
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'oke2'/'lib'))
import cluster3 as c3

class Cluster3Tests(unittest.TestCase):
    def setUp(self):
        proc=subprocess.run(['bash','-c','source lib/common.sh; python3 -c "import os,json; print(json.dumps(dict(os.environ)))"'],cwd=ROOT,env={**os.environ,'MYSQLK8S_CONFIG':'/nonexistent'},text=True,capture_output=True,check=True)
        env=json.loads(proc.stdout)
        env.update(C1_CONTEXT='c1',C2_CONTEXT='c2',C1_KUBECONFIG='/home/opc/.kube/config',C2_KUBECONFIG='/home/opc/.kube/config2',
                   C1_SOURCE_CLUSTER='mycluster',C1_SOURCE_NAMESPACE='mysql',CLUSTER3_NAME='mycluster3',CLUSTER3_NAMESPACE='mysql-tertiary',
                   CLUSTER3_SECRET='mycluster3-root',CLUSTER3_BASE_SERVER_ID='3000',CLUSTER3_INSTANCES='3',CLUSTER3_ROUTERS='2',
                   CLUSTER3_STORAGE_CLASS='oci-bv',CLUSTER3_STORAGE_SIZE='10Gi',K8S_CLUSTER_DOMAIN='cluster.local',C1_DNS_SERVERS='',C2_DNS_SERVERS='')
        self.env=patch.dict(os.environ,env,clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.primary={'metadata':{'name':'mycluster2','namespace':'mysql-secondary'},'spec':{'secretName':'mycluster2-root','version':'26.7.0','edition':'community','baseServerId':2000}}
        self.data={k:base64.b64encode(v.encode()).decode() for k,v in {'rootUser':'root','rootHost':'%','rootPassword':'Secret-example-123'}.items()}

    def test_kubectl_uses_explicit_file_and_context(self):
        with patch.object(c3.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{}','')) as run:
            c3.k(2,'get','pods',obj=True)
        self.assertEqual(run.call_args.args[0][:5],['kubectl','--kubeconfig','/home/opc/.kube/config2','--context','c2'])

    def test_same_api_rejected_before_mutation(self):
        def fake(which,*args,**kw):
            return {'clusters':[{'cluster':{'server':'https://same:6443'}}]} if args[0]=='config' else 'ok'
        with patch.object(c3,'k',side_effect=fake) as k,redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(c3.Error,'same API'):c3.identity()
        self.assertTrue(all(call.args[1] in ('config','get') for call in k.call_args_list))

    def test_native_replica_uses_live_primary_and_unique_server_ids(self):
        target='mycluster2-1.mycluster2-instances.mysql-secondary.svc.cluster.local:3306'
        doc=c3.build_manifest(self.primary,target)
        self.assertEqual(doc['metadata'],{'name':'mycluster3','namespace':'mysql-tertiary'})
        self.assertEqual(doc['spec']['initDB']['clusterSet']['targetUrl'],target)
        self.assertEqual(doc['spec']['baseServerId'],3000)
        self.assertEqual(doc['spec']['version'],'26.7.0')
        self.assertNotIn('Secret-example',json.dumps(doc))
        self.assertEqual(os.environ['MYSQL_CLUSTER_NAME'],'mycluster')

    def test_overlapping_server_ids_rejected_across_okes(self):
        doc=c3.build_manifest(self.primary,'source:3306');other=copy.deepcopy(self.primary);other['spec']['baseServerId']=3050
        with self.assertRaisesRegex(c3.Error,'overlaps'):c3.check_ids(doc,[other])
        c3.check_ids(doc,[self.primary])

    def test_password_only_on_stdin(self):
        with patch.object(c3,'k',return_value='CLUSTER3_JSON={"ok":true}') as k:
            self.assertEqual(c3.shell(1,self.primary,self.data,'pass'),{'ok':True})
        self.assertNotIn('Secret-example-123',json.dumps(k.call_args.args))
        self.assertEqual(k.call_args.kwargs['payload'],'Secret-example-123\n')
        self.assertIn('--passwords-from-stdin',k.call_args.args)

    def test_namespace_forwarding_is_specific_and_deterministic(self):
        text=c3.forwarding({'mysql-secondary.svc.cluster.local','mysql.svc.cluster.local'},['10.60.10.20','10.60.10.21'])
        self.assertIn('mysql.svc.cluster.local:53 {',text)
        self.assertIn('forward . 10.60.10.20 10.60.10.21',text)
        self.assertNotIn('\ncluster.local:53',text)
        self.assertNotIn('kube-system',text)

    def test_dns_rejects_shadowing_existing_c1_namespace(self):
        def fake(which,kind,*args,**kwargs):
            return {'items':[]} if kind=='services' else {'metadata':{'name':'mysql-tertiary'}}
        with patch.object(c3,'get',side_effect=fake):
            with self.assertRaisesRegex(c3.Error,'shadow local DNS'):c3.dns_plan([self.primary])

    def test_dns_rejects_shadowing_c2_services(self):
        with patch.object(c3,'get',return_value={'items':[{'metadata':{'name':'local-service'}}]}):
            with self.assertRaisesRegex(c3.Error,'shadow them'):c3.dns_plan([self.primary])

    def test_ready_dns_ignores_terminating_and_unready_pods(self):
        def pod(ip,ready=True,terminating=False):
            return {'metadata':{'deletionTimestamp':'now'} if terminating else {},'status':{'podIP':ip,'conditions':[{'type':'Ready','status':'True' if ready else 'False'}]}}
        with patch.object(c3,'k',return_value={'items':[pod('10.0.0.1'),pod('10.0.0.2',False),pod('10.0.0.3',True,True)]}):
            self.assertEqual(c3.ready_dns(1),['10.0.0.1'])

    def test_probe_pod_is_cleaned_on_connectivity_failure(self):
        source={'spec':{'containers':[{'name':'sidecar','image':'operator:26.7.0'}]}}
        with patch.object(c3,'get',return_value=source),patch.object(c3,'k',side_effect=['created',c3.Error('readiness failed'),'deleted']) as k:
            with self.assertRaisesRegex(c3.Error,'readiness failed'):c3.temporary_probe(self.primary,{'x:3306':None})
        self.assertIn('delete',k.call_args.args)

    def test_namespace_and_invalid_dns_configuration_rejected(self):
        os.environ['CLUSTER3_NAMESPACE']='kube-system'
        with self.assertRaisesRegex(c3.Error,'dedicated'):c3.config()

    def receiver_status(self, primary=0):
        endpoints=[f'mycluster3-{i}.mycluster3-instances.mysql-tertiary.svc.cluster.local:3306' for i in range(3)]
        return {'clusterRole':'REPLICA','clusterSetReplication':{'receiver':endpoints[primary]},
                'topology':{ep:{'address':ep,'status':'ONLINE','memberRole':'PRIMARY' if i==primary else 'SECONDARY'}
                            for i,ep in enumerate(endpoints)}}

    def test_replica_receiver_without_top_level_primary(self):
        member=self.receiver_status()
        self.assertNotIn('primary',member)
        self.assertEqual(c3.replication_receiver(member,'mycluster3'),'mycluster3-0')

    def test_replica_receiver_follows_primary_member_change(self):
        self.assertEqual(c3.replication_receiver(self.receiver_status(2),'mycluster3'),'mycluster3-2')

    def test_replica_receiver_falls_back_to_group_topology(self):
        member=self.receiver_status(1);del member['clusterSetReplication']
        self.assertEqual(c3.replication_receiver(member,'mycluster3'),'mycluster3-1')

    def test_stale_receiver_and_ambiguous_topology_rejected(self):
        member=self.receiver_status(1)
        member['clusterSetReplication']['receiver']=next(iter(member['topology']))
        with self.assertRaisesRegex(c3.Error,'disagree'):c3.replication_receiver(member,'mycluster3')
        member=self.receiver_status()
        for details in member['topology'].values():details['memberRole']='SECONDARY'
        with self.assertRaisesRegex(c3.Error,'exactly one'):c3.replication_receiver(member,'mycluster3')

if __name__=='__main__':unittest.main()
