"""Image build/import reach their remote stage without a target IP or DNS gate."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ImagePreparationSequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.state=self.folder/'state';self.state.mkdir()
        self.trace=self.folder/'ssh-trace'
        ssh=self.folder/'ssh';ssh.write_text('#!/usr/bin/env python3\nimport os,sys,json\nwith open(os.environ["SEQUENCE_TRACE"],"a") as f:f.write(json.dumps({"args":sys.argv[1:],"script":sys.stdin.read()})+"\\n")\n');ssh.chmod(0o700)
        self.config=self.folder/'community.env'
        values=dict(ADMIN_HOST='192.0.2.10',ADMIN_USER='testadmin',ADMIN_WORKDIR='/home/testadmin/new-site',
                    CLUSTER_NAME='okd-test',BASE_DOMAIN='lab.test',MACHINE_CIDR='198.51.100.0/24',
                    POD_CIDR='10.132.0.0/14',SERVICE_CIDR='172.31.0.0/16',INSTALL_DISK='/dev/sda',
                    OCI_REGION='test-region',OCI_COMPARTMENT_ID='ocid1.compartment.synthetic',
                    OCI_COMPARTMENT_NAME='Synthetic',OCI_BUCKET='synthetic-bucket',OCI_NAMESPACE='synthetic-namespace')
        self.config.write_text(''.join(k+'='+shlex.quote(v)+'\n' for k,v in values.items()))
        (self.state/'host.env').write_text('SSH_HOST=192.0.2.10\nSSH_USER=testadmin\nTARGET_HOST=\n')
        self.env={k:v for k,v in os.environ.items() if k not in ['NODE_IP','OKD_REGION','OKD_SELECTED_REGION','KUBECONFIG']}
        self.env.update(OKD_CONFIG_FILE=str(self.config),OKD_STATE_DIR=str(self.state),PATH=str(self.folder)+os.pathsep+os.environ['PATH'],SEQUENCE_TRACE=str(self.trace))

    def test_build_without_node_ip_or_dns_reaches_media_generation(self):
        r=subprocess.run(['bash',str(ROOT/'openshift/community/03-build-control-plane-image.sh')],env=self.env,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        payload=json.loads(self.trace.read_text())['script']
        self.assertIn('create single-node-ignition-config',payload)
        self.assertNotIn('dig +short',payload)
        self.assertNotIn('> expected-node-ip',payload)
        self.assertIn('node_ip_required=False',payload)

    def test_upload_import_without_node_ip_reaches_scoped_import_helper(self):
        r=subprocess.run(['bash',str(ROOT/'openshift/community/04-upload-image.sh')],env=self.env,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        trace=json.loads(self.trace.read_text())
        self.assertIn('InstancePrincipalsSecurityTokenSigner',trace['script'])
        self.assertIn('compute.create_image',trace['script'])
        self.assertIn('test-region',' '.join(trace['args']))

    def test_bootstrap_monitoring_still_requires_registered_target(self):
        # Legacy profiles are validated by the cluster checks; regional profiles
        # additionally require the selected address before full validation.
        common=ROOT/'openshift/community/lib/common.sh'
        r=subprocess.run(['bash','-c','source "$1"; load_config preparation; validate_cluster','--',str(common)],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(self.trace.exists())


if __name__=='__main__':unittest.main()
