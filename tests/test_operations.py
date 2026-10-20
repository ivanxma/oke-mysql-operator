#!/usr/bin/env python3
"""Operation planning, stale-plan guards, and actual replication source matching."""
import argparse
import base64
from contextlib import nullcontext, redirect_stdout
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import operations as op


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.pair = {"mycluster": {"name": "mycluster", "namespace": "mysql"},
                     "mycluster2": {"name": "mycluster2", "namespace": "mysql-secondary"}}
        self.status = {"domainName": "mycluster", "primaryCluster": "mycluster", "status": "HEALTHY", "clusters": {}}
        for cluster in self.pair:
            members = {f"{cluster}-{i}.example:3306": {"address": f"{cluster}-{i}.example:3306", "status": "ONLINE",
                       "memberRole": "PRIMARY" if i == 0 else "SECONDARY"} for i in range(3)}
            self.status["clusters"][cluster] = {"clusterRole": "PRIMARY" if cluster == "mycluster" else "REPLICA",
                "globalStatus": "OK", "primary": f"{cluster}-0.example:3306", "topology": members}

    def test_toggle_first_to_second(self):
        plan = op.plan_operation("toggle", self.pair, self.status, 120)
        self.assertEqual(plan["targetCluster"], "mycluster2")

    def test_toggle_second_to_first(self):
        self.status["primaryCluster"] = "mycluster2"
        plan = op.plan_operation("toggle", self.pair, self.status, 120)
        self.assertEqual(plan["targetCluster"], "mycluster")

    def three_cluster_status(self, primary="mycluster"):
        status = copy.deepcopy(self.status)
        status["clusters"]["mycluster3"] = copy.deepcopy(status["clusters"]["mycluster2"])
        status["primaryCluster"] = primary
        for name, member in status["clusters"].items():
            member["clusterRole"] = "PRIMARY" if name == primary else "REPLICA"
        return status

    def test_cycle_all_three_directions(self):
        for current, target in (("mycluster", "mycluster2"), ("mycluster2", "mycluster3"), ("mycluster3", "mycluster")):
            with self.subTest(current=current):
                status = self.three_cluster_status(current)
                op.validate_health(status, self.pair, allowed_primary=status["clusters"])
                plan = op.plan_operation("toggle", self.pair, status, 120)
                self.assertEqual(plan["targetCluster"], target)

    def test_numeric_targets_from_any_primary(self):
        for current in ("mycluster", "mycluster2", "mycluster3"):
            for number, target in enumerate(("mycluster", "mycluster2", "mycluster3"), 1):
                with self.subTest(current=current, number=number):
                    plan = op.plan_operation("toggle", self.pair, self.three_cluster_status(current), 120, target=str(number))
                    self.assertEqual(plan["targetCluster"], target)

    def test_missing_or_invalid_target_refused(self):
        with self.assertRaisesRegex(op.cs.SetupError, "not a registered"):
            op.plan_operation("toggle", self.pair, self.status, 120, target="3")
        with self.assertRaisesRegex(op.cs.SetupError, "1, 2 or 3"):
            op.plan_operation("toggle", self.pair, self.status, 120, target="4")

    def test_already_primary_does_not_submit_mutation(self):
        with patch.object(op, "operation_lock", return_value=nullcontext()), \
                patch.object(op, "load_pair", return_value=(self.pair, self.status)), \
                patch.object(op, "run_shell") as run, redirect_stdout(io.StringIO()) as output:
            op.execute("toggle", argparse.Namespace(timeout=120, dry_run=False, target="1"))
            run.assert_not_called()
            self.assertIn("already primary", output.getvalue())

    def test_switch_back_from_cluster3_uses_c1_anchor_once(self):
        status = self.three_cluster_status("mycluster3")
        result = self.three_cluster_status("mycluster")
        pair = copy.deepcopy(self.pair)
        for value in pair.values():value["data"] = {"rootPassword":"unused-test-data"}
        with patch.object(op, "operation_lock", return_value=nullcontext()), \
                patch.object(op, "load_pair", return_value=(pair, status)), \
                patch.object(op, "run_shell", return_value=result) as run, redirect_stdout(io.StringIO()):
            op.execute("toggle", argparse.Namespace(timeout=120, dry_run=False, target=None))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[:2], ("mysql", "mycluster-0"))
            self.assertIn("set_primary_cluster", run.call_args.args[3])
            self.assertNotIn("force_primary_cluster", run.call_args.args[3])

    def test_third_unhealthy_blocks_explicit_target(self):
        status = self.three_cluster_status()
        status["clusters"]["mycluster3"]["globalStatus"] = "NOT_OK"
        with self.assertRaisesRegex(op.cs.SetupError, "globally OK"):
            op.validate_health(status, self.pair, allowed_primary=status["clusters"])

    def test_first_secondary_is_lowest_ordinal_excluding_primary(self):
        plan = op.plan_operation("rotate", self.pair, self.status, 120)
        self.assertEqual(plan["targetInstance"], "mycluster-1.example:3306")
        topology = self.status["clusters"]["mycluster"]["topology"]
        topology["mycluster-0.example:3306"]["memberRole"] = "SECONDARY"
        topology["mycluster-1.example:3306"]["memberRole"] = "PRIMARY"
        self.assertEqual(op.select_secondary(topology, "mycluster"), "mycluster-0.example:3306")

    def test_rotate_follows_actual_primary_cluster(self):
        self.status["primaryCluster"] = "mycluster2"
        plan = op.plan_operation("rotate", self.pair, self.status, 120)
        self.assertEqual(plan["targetInstance"], "mycluster2-1.example:3306")

    def test_unhealthy_and_missing_clusters_rejected(self):
        op.validate_health(self.status, self.pair)
        self.status["status"] = "AVAILABLE"
        with self.assertRaisesRegex(op.cs.SetupError, "HEALTHY"):
            op.validate_health(self.status, self.pair)
        self.status["status"] = "HEALTHY"
        with self.assertRaisesRegex(op.cs.SetupError, "missing"):
            op.validate_health(self.status, {**self.pair, "another": {}})

    def test_third_healthy_replica_follows_pair_toggle(self):
        extra = copy.deepcopy(self.status["clusters"]["mycluster2"])
        self.status["clusters"]["mycluster3"] = extra
        op.validate_health(self.status, self.pair)
        plan = op.plan_operation("toggle", self.pair, self.status, 120)
        self.assertEqual(plan["targetCluster"], "mycluster2")
        extra["globalStatus"] = "NOT_OK"
        with self.assertRaisesRegex(op.cs.SetupError, "globally OK"):
            op.validate_health(self.status, self.pair)

    def test_primary_outside_pair_is_not_toggled(self):
        self.status["primaryCluster"] = "mycluster3"
        with self.assertRaisesRegex(op.cs.SetupError, "configured pair"):
            op.validate_health(self.status, self.pair)

    def test_no_secondary_stops_rotation(self):
        topology = {"mycluster-0.example:3306": {"status": "ONLINE", "memberRole": "PRIMARY"}}
        with self.assertRaisesRegex(op.cs.SetupError, "No ONLINE secondary"):
            op.select_secondary(topology, "mycluster")

    def test_mutations_recheck_role_and_have_no_emergency_fallback(self):
        for action, method in (("toggle", "set_primary_cluster"), ("rotate", "set_primary_instance")):
            plan = op.plan_operation(action, self.pair, self.status, 120)
            code = op.mutation_code(plan)
            compile(code, "operation", "exec")
            self.assertIn(method, code)
            self.assertIn("Primary cluster changed since planning", code)
            self.assertNotIn("force_primary_cluster", code)
            self.assertNotIn("invalidateReplicaClusters", code)

    def test_dry_run_does_not_submit_mutation(self):
        with patch.object(op, "operation_lock", return_value=nullcontext()), \
                patch.object(op, "load_pair", return_value=(self.pair, self.status)), \
                patch.object(op, "run_shell") as run, redirect_stdout(io.StringIO()):
            op.execute("toggle", argparse.Namespace(timeout=120, dry_run=True))
            run.assert_not_called()

    def test_verification_requires_new_roles(self):
        plan = op.plan_operation("toggle", self.pair, self.status, 120)
        self.assertFalse(op.switched(self.status, plan))
        new = copy.deepcopy(self.status)
        new["primaryCluster"] = "mycluster2"
        new["clusters"]["mycluster"]["clusterRole"] = "REPLICA"
        new["clusters"]["mycluster2"]["clusterRole"] = "PRIMARY"
        self.assertTrue(op.switched(new, plan))

    def test_channel_actual_uuid_overrides_configured_host(self):
        channel = {"Source_Host": "old.example", "Source_UUID": "old", "Replica_IO_Running": "Yes"}
        connection = {"SOURCE_UUID": "new", "SERVICE_STATE": "ON"}
        match, host = op.source_match(channel, connection, {"new": "new.example:3306"}, "new.example:3306")
        self.assertEqual((match, host), ("MATCH", "new.example:3306"))

    def test_channel_disconnected_is_not_reported_as_match(self):
        channel = {"Source_UUID": "current", "Replica_IO_Running": "Connecting"}
        self.assertEqual(op.source_match(channel, None, {"current": "node:3306"}, "node:3306")[0], "NOT CONNECTED")

    def test_channel_stale_source_is_visible(self):
        channel = {"Source_UUID": "old", "Replica_IO_Running": "Yes"}
        self.assertEqual(op.source_match(channel, None, {"old": "old:3306"}, "new:3306")[0], "DIFFERENT SOURCE")

    def test_password_only_in_stdin(self):
        data = {"rootPassword": base64.b64encode(b"secret-test").decode()}
        response = "\x1b[1mPlease provide the password for 'root@127.0.0.1:3306': \x1b[0mMYSQLK8S_JSON={\"ok\":true}"
        with patch.object(op.cs, "kubectl", return_value=response) as command, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(op.run_shell("mysql", "mycluster-0", data, "pass"), {"ok": True})
            self.assertNotIn("secret-test", str(command.call_args.args))
            self.assertEqual(command.call_args.kwargs["payload"], "secret-test\n")
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
