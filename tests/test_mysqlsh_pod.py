#!/usr/bin/env python3
"""Validate the Shell client's network, image, and isolation settings."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from mysqlsh_pod import build_pod


class MySQLShellPodTests(unittest.TestCase):
    def source(self):
        return {"spec": {"containers": [
            {"name": "mysql", "image": "mysql-server:example"},
            {"name": "sidecar", "image": "mysql-operator:example"}],
            "volumes": [{"name": "datadir", "persistentVolumeClaim": {"claimName": "database"}}]}}

    def test_cluster_dns_and_selected_namespace(self):
        pod = build_pod(self.source(), "mysql-secondary", "client")
        self.assertEqual(pod["metadata"]["namespace"], "mysql-secondary")
        self.assertEqual(pod["spec"]["dnsPolicy"], "ClusterFirst")
        self.assertFalse(pod["spec"]["hostNetwork"])

    def test_reuses_operator_image_without_database_storage_or_token(self):
        pod = build_pod(self.source(), "mysql", "client")
        spec = pod["spec"]
        self.assertEqual(spec["containers"][0]["image"], "mysql-operator:example")
        self.assertEqual(spec["volumes"], [{"name": "tmp", "emptyDir": {}}])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertFalse(spec["containers"][0]["securityContext"]["allowPrivilegeEscalation"])

    def test_private_image_pull_references_preserved(self):
        source = self.source()
        source["spec"]["imagePullSecrets"] = [{"name": "registry"}]
        pod = build_pod(source, "mysql", "client")
        self.assertEqual(pod["spec"]["imagePullSecrets"], [{"name": "registry"}])

    def test_database_without_sidecar_rejected(self):
        source = self.source()
        source["spec"]["containers"] = source["spec"]["containers"][:1]
        with self.assertRaisesRegex(ValueError, "no MySQL Operator sidecar"):
            build_pod(source, "mysql", "client")


if __name__ == "__main__":
    unittest.main(verbosity=2)
