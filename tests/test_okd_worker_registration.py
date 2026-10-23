"""CSR approval is restricted to the expected OCI worker identity and purpose."""
import base64
import datetime
import importlib.util
import ipaddress
from pathlib import Path
import unittest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

spec=importlib.util.spec_from_file_location('worker_registration',Path(__file__).resolve().parents[1]/'openshift/community/lib/register-worker.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


class WorkerCSRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def setUp(self):
        self.now=datetime.datetime.now(datetime.timezone.utc);self.host='worker.lab.test';self.address='192.0.2.20'
        self.node={'metadata':{'name':self.host},'status':{'addresses':[{'type':'InternalIP','address':self.address}]}}

    def request(self,serving=False,host=None,organization='system:nodes',address=None,ca=False):
        host=host or self.host
        b=x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'system:node:'+host),x509.NameAttribute(NameOID.ORGANIZATION_NAME,organization)]))
        if serving:b=b.add_extension(x509.SubjectAlternativeName([x509.DNSName(host),x509.IPAddress(ipaddress.ip_address(address or self.address))]),critical=False)
        if ca:b=b.add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        csr=b.sign(self.key,hashes.SHA256())
        return dict(metadata=dict(name='csr-synthetic',uid='csr-uid',resourceVersion='1',creationTimestamp=self.now.isoformat()),
                    spec=dict(request=base64.b64encode(csr.public_bytes(serialization.Encoding.PEM)).decode(),
                    signerName='kubernetes.io/kubelet-serving' if serving else 'kubernetes.io/kube-apiserver-client-kubelet',
                    username='system:node:'+host if serving else 'system:serviceaccount:openshift-machine-config-operator:node-bootstrapper',
                    groups=['system:nodes'] if serving else ['system:serviceaccounts'],usages=['server auth'] if serving else ['client auth']))

    def plan(self,csr):return worker.csr_plan(csr,self.host,self.address,self.node,self.now-datetime.timedelta(minutes=5))

    def test_bootstrap_and_serving_requests_for_verified_worker_are_eligible(self):
        self.assertIn('bootstrap',self.plan(self.request()))
        self.assertIn('serving',self.plan(self.request(serving=True)))

    def test_another_node_is_not_approved(self):self.assertIsNone(self.plan(self.request(host='another.lab.test')))

    def test_admin_group_and_unexpected_extensions_are_rejected(self):
        with self.assertRaises(ValueError):self.plan(self.request(organization='system:masters'))
        with self.assertRaises(ValueError):self.plan(self.request(serving=True,ca=True))

    def test_serving_request_cannot_claim_another_ip(self):
        with self.assertRaises(ValueError):self.plan(self.request(serving=True,address='192.0.2.21'))

    def test_another_requestor_or_signer_is_rejected(self):
        csr=self.request();csr['spec']['username']='system:serviceaccount:unrelated:account'
        with self.assertRaises(ValueError):self.plan(csr)
        csr=self.request();csr['spec']['signerName']='kubernetes.io/kube-apiserver-client'
        with self.assertRaises(ValueError):self.plan(csr)

    def test_denied_or_already_approved_requests_are_skipped(self):
        for status in ['Denied','Approved']:
            csr=self.request();csr['status']={'conditions':[{'type':status}]}
            self.assertIsNone(self.plan(csr))

    def test_csr_from_before_vm_creation_is_rejected(self):
        csr=self.request();csr['metadata']['creationTimestamp']=(self.now-datetime.timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):self.plan(csr)

    def test_serving_node_must_match_actual_private_address(self):
        self.node['status']['addresses'][0]['address']='192.0.2.21'
        with self.assertRaises(ValueError):self.plan(self.request(serving=True))


if __name__=='__main__':unittest.main()
