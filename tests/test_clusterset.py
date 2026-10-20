#!/usr/bin/env python3
"""ClusterSet safeguards and bootstrap behavior, with no Kubernetes mutations."""
import base64
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import clusterset as cs


def defaults():
    result = subprocess.run(["bash", "-c", 'source lib/clusterset_common.sh; python3 -c "import json,os; print(json.dumps(dict(os.environ)))"'],
                            cwd=ROOT, capture_output=True, text=True, check=True,
                            env={**os.environ, "MYSQLK8S_CONFIG": "/nonexistent", "MYSQLK8S_CLUSTERSET_CONFIG": "/nonexistent"})
    return json.loads(result.stdout)


class ClusterSetTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, defaults(), clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.primary = {"metadata": {"name": "mycluster", "namespace": "mysql"},
                        "spec": {"version": "26.7.0", "edition": "community", "secretName": "mycluster-root"}}
        self.data = {k: base64.b64encode(v.encode()).decode() for k, v in
                     {"rootUser": "root", "rootHost": "%", "rootPassword": "test-password-1234"}.items()}

    def manifest(self):
        return cs.build_replica(self.primary, "oci-bv")

    def test_native_bootstrap_separate_namespace_and_server_ids(self):
        doc = self.manifest()
        self.assertEqual(doc["metadata"], {"name": "mycluster2", "namespace": "mysql-secondary"})
        self.assertEqual(doc["spec"]["baseServerId"], 2000)
        self.assertEqual(doc["spec"]["initDB"]["clusterSet"]["targetUrl"],
                         "mycluster-0.mycluster-instances.mysql.svc.cluster.local:3306")
        self.assertEqual(doc["spec"]["initDB"]["clusterSet"]["secretKeyRef"]["name"], "mycluster2-root")
        self.assertNotIn("test-password-1234", json.dumps(doc))
        self.assertEqual(os.environ["MYSQL_NAMESPACE"], "mysql")

    def test_same_namespace_rejected(self):
        os.environ["REPLICA_MYSQL_NAMESPACE"] = "mysql"
        with self.assertRaisesRegex(cs.SetupError, "different namespace"):
            cs.settings()

    def test_duplicate_cluster_name_rejected(self):
        os.environ["REPLICA_MYSQL_CLUSTER_NAME"] = "mycluster"
        with self.assertRaisesRegex(cs.SetupError, "distinct"):
            cs.settings()

    def test_independent_existing_cluster_rejected(self):
        doc = self.manifest()
        existing = copy.deepcopy(doc)
        del existing["spec"]["initDB"]
        with self.assertRaisesRegex(cs.SetupError, "initDB"):
            cs.validate_existing(existing, doc)

    def test_matching_existing_bootstrap_is_idempotent(self):
        cs.validate_existing(self.manifest(), self.manifest())

    def test_changed_storage_rejected(self):
        doc = self.manifest()
        existing = copy.deepcopy(doc)
        existing["spec"]["datadirVolumeClaimTemplate"]["storageClassName"] = "other"
        with self.assertRaisesRegex(cs.SetupError, "storage differs"):
            cs.validate_existing(existing, doc)

    def test_wrong_primary_not_promoted(self):
        status = {"clusterName": "mycluster", "clusterSet": {"primaryCluster": "another"}}
        with self.assertRaisesRegex(cs.SetupError, "No roles were changed"):
            cs.primary_role(status, "mycluster")

    def test_password_passed_only_via_stdin(self):
        result = {"clusterName": "mycluster", "clusterSet": {}}
        with patch.object(cs, "kubectl", return_value="prompt MYSQLK8S_JSON=" + json.dumps(result)) as mock:
            self.assertEqual(cs.admin_status("mysql", "mycluster", self.data), result)
            args, kwargs = mock.call_args
            self.assertNotIn("test-password-1234", json.dumps(args))
            self.assertEqual(kwargs["payload"], "test-password-1234\n")
            self.assertIn("--passwords-from-stdin", args)

    def test_secret_and_cluster_creation_order(self):
        doc = self.manifest()
        def fake_get(kind, *args, **kwargs):
            return {"items": []} if kind == "pvc" else None
        with tempfile.TemporaryDirectory() as temp, patch.object(cs, "ROOT", Path(temp)), \
                patch.object(cs, "check", return_value=(self.primary, doc, self.data, None)), \
                patch.object(cs, "get", side_effect=fake_get), patch.object(cs, "kubectl", return_value="") as mock, \
                redirect_stdout(io.StringIO()):
            cs.create()
            calls = mock.call_args_list
            dryrun = next(i for i, c in enumerate(calls) if "--dry-run=server" in c.args)
            creates = [(i, json.loads(c.kwargs["payload"])) for i, c in enumerate(calls)
                       if c.args[:3] == ("create", "-f", "-")]
            self.assertEqual([d["kind"] for _, d in creates], ["Secret", "InnoDBCluster"])
            self.assertLess(dryrun, creates[0][0])
            self.assertEqual(creates[0][1]["data"], self.data)
            manifest = (Path(temp) / "generated/replica-innodbcluster.json").read_text()
            self.assertNotIn(self.data["rootPassword"], manifest)

    def test_existing_secret_never_overwritten(self):
        doc = self.manifest()
        with patch.object(cs, "check", return_value=(self.primary, doc, self.data, doc)), \
                patch.object(cs, "get", return_value={"data": {"rootPassword": "different"}}), \
                patch.object(cs, "kubectl") as mock:
            with self.assertRaisesRegex(cs.SetupError, "credentials"):
                cs.create()
            mock.assert_not_called()

    def test_orphan_pvcs_not_reused(self):
        def fake_get(kind, *args, **kwargs):
            if kind == "namespace": return {"metadata": {"name": "mysql-secondary"}}
            if kind == "pvc": return {"items": [{"metadata": {"name": "datadir-mycluster2-0"}}]}
        with patch.object(cs, "check", return_value=(self.primary, self.manifest(), self.data, None)), \
                patch.object(cs, "get", side_effect=fake_get), patch.object(cs, "kubectl") as mock:
            with self.assertRaisesRegex(cs.SetupError, "PVCs already exist"):
                cs.create()
            mock.assert_not_called()

    def verify_with(self, replica_role="REPLICA", read_only=1):
        status = {"clusterName": "mycluster", "clusterSet": {
            "domainName": "mycluster", "primaryCluster": "mycluster", "status": "HEALTHY",
            "clusters": {"mycluster": {"globalStatus": "OK", "clusterRole": "PRIMARY"},
                         "mycluster2": {"globalStatus": "OK", "clusterRole": replica_role}}}}
        replica = {"spec": {"instances": 3}, "status": {"cluster": {"status": "ONLINE", "onlineInstances": 3}}}
        local = {"clusterSet": status["clusterSet"], "superReadOnly": read_only}
        with patch.object(cs, "get", side_effect=[replica, self.primary]), \
                patch.object(cs, "credentials", return_value=self.data), \
                patch.object(cs, "admin_status", side_effect=[status, local]), \
                patch.object(cs, "kubectl", return_value="") as command, redirect_stdout(io.StringIO()) as output:
            cs.verify()
            return output.getvalue(), command.call_args_list

    def test_verify_checks_real_roles_and_read_only(self):
        output, calls = self.verify_with()
        self.assertIn("ClusterSet HEALTHY, replica read-only", output)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("rollout" in c.args for c in calls))

    def test_verify_rejects_wrong_replica_role(self):
        with self.assertRaisesRegex(cs.SetupError, "healthy ClusterSet REPLICA"):
            self.verify_with(replica_role="PRIMARY")

    def test_verify_rejects_writable_replica(self):
        with self.assertRaisesRegex(cs.SetupError, "super_read_only"):
            self.verify_with(read_only=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
