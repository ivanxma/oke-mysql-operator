#!/usr/bin/env python3
"""Planned ClusterSet/member switchovers and read-only channel monitoring."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import clusterset as cs


def canonical(name):
    return name.replace("-", "_").replace(".", "_")


def endpoints():
    pn, pc, rn, rc, _ = cs.settings()
    return {canonical(pc): (pn, pc), canonical(rc): (rn, rc)}


def run_shell(namespace, pod, data, body, timeout=90):
    code = 'import json\n' + body
    output = cs.kubectl("--request-timeout=0", "-n", namespace, "exec", "-i", pod, "-c", "sidecar", "--",
                        "mysqlsh", "--uri", "root@127.0.0.1:3306", "--passwords-from-stdin", "--py",
                        "--quiet-start=2", "-e", code,
                        payload=base64.b64decode(data["rootPassword"]).decode() + "\n", timeout=timeout)
    marker = "MYSQLK8S_JSON="
    cs.require(marker in output, "MySQL Shell did not return an operation result.")
    # Keep useful AdminAPI progress messages while omitting the password prompt.
    prefix = output.split(marker, 1)[0]
    prefix = re.sub(r"\x1b\[[0-9;]*m", "", prefix)
    prefix = re.sub(r"Please provide the password for '[^']*':\s*", "", prefix)
    if prefix.strip():
        print(prefix.strip(), flush=True)
    return json.JSONDecoder().raw_decode(output.split(marker, 1)[1])[0]


def load_pair(healthy=True, allow_third=False):
    pair = {}
    allowed = set(endpoints())
    if allow_third:
        third = os.environ.get("CLUSTER3_NAME", "mycluster3")
        cs.require(len(third) <= 40 and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", third), "Invalid CLUSTER3_NAME.")
        cs.require(canonical(third) not in allowed, "CLUSTER3_NAME must differ from the configured c1 pair.")
        allowed.add(canonical(third))
    for key, (namespace, name) in endpoints().items():
        cluster = cs.get("innodbcluster", name, namespace)
        data = cs.credentials(cluster)
        status = cs.admin_status(namespace, name, data)
        if healthy:
            state = cluster.get("status", {}).get("cluster", {})
            cs.require(state.get("status") == "ONLINE" and state.get("onlineInstances") == cluster["spec"]["instances"],
                       f"{namespace}/{name} does not have all configured members ONLINE.")
            validate_health(status["clusterSet"], endpoints(), allowed_primary=allowed)
        pair[key] = {"namespace": namespace, "name": name, "data": data, "status": status}
    statuses = [v["status"]["clusterSet"] for v in pair.values()]
    cs.require(len({s["domainName"] for s in statuses}) == 1 and len({s.get("primaryCluster") for s in statuses}) == 1,
               "The two clusters disagree on ClusterSet identity or primary. Retry after topology converges.")
    cs.require(statuses[0].get("primaryCluster") in allowed, "Current primary is outside the configured operation targets.")
    return pair, statuses[0]


def validate_health(status, expected, allowed_primary=None):
    cs.require(status.get("status") == "HEALTHY", "ClusterSet must be HEALTHY before a planned switchover.")
    members = status.get("clusters", {})
    cs.require(set(expected).issubset(members), "ClusterSet is missing a configured member of the toggle pair.")
    primary = status.get("primaryCluster")
    cs.require(primary in (allowed_primary if allowed_primary is not None else expected), "ClusterSet primary is not in the configured pair or allowed targets.")
    for name, member in members.items():
        cs.require(member.get("globalStatus") == "OK", f"ClusterSet member {name} is not globally OK.")
        cs.require(member.get("clusterRole") == ("PRIMARY" if name == primary else "REPLICA"),
                   f"Unexpected role for {name}.")
        cs.require(member.get("topology") and all(n.get("status") == "ONLINE" for n in member["topology"].values()),
                   f"Cluster {name} has non-ONLINE members.")


def select_secondary(topology, cluster_name):
    candidates = []
    for endpoint, member in topology.items():
        address = member.get("address", endpoint)
        match = re.match(re.escape(cluster_name) + r"-(\d+)\.", address)
        if match and member.get("status") == "ONLINE" and member.get("memberRole") == "SECONDARY":
            candidates.append((int(match.group(1)), address))
    cs.require(candidates, "No ONLINE secondary group member is available for primary-node rotation.")
    return min(candidates)[1]


def plan_operation(operation, pair, status, timeout, target=None):
    current = status["primaryCluster"]
    plan = {"operation": operation, "domainName": status["domainName"],
            "currentPrimaryCluster": current, "timeout": timeout}
    if operation == "toggle":
        names = list(pair)
        choices = {"1": names[0], "2": names[1], "3": canonical(os.environ.get("CLUSTER3_NAME", "mycluster3"))}
        if target is not None:
            cs.require(str(target) in choices, "Target must be 1, 2 or 3.")
            selected = choices[str(target)]
            cs.require(selected in status["clusters"], "Selected cluster is not a registered ClusterSet member: " + selected)
        else:
            cycle = [name for name in choices.values() if name in status["clusters"]]
            cs.require(current in cycle, "Current primary is outside the configured cycle.")
            selected = cycle[(cycle.index(current) + 1) % len(cycle)]
        plan["targetCluster"] = selected
    else:
        member = status["clusters"][current]
        plan["currentPrimaryInstance"] = member["primary"]
        plan["targetInstance"] = select_secondary(member["topology"], pair[current]["name"])
    return plan


def mutation_code(plan):
    # Recheck the live topology inside the same Shell invocation as the operation.
    # A stale plan fails rather than choosing a new toggle target automatically.
    code = "p=json.loads(" + repr(json.dumps(plan)) + ")\nc=dba.get_cluster()\ns=c.get_cluster_set()\nlive=s.status({'extended':1})\n"
    code += "if live.get('domainName') != p['domainName'] or live.get('primaryCluster') != p['currentPrimaryCluster']: raise RuntimeError('Primary cluster changed since planning; no switchover submitted.')\n"
    code += "if live.get('status') != 'HEALTHY' or any(v.get('globalStatus') != 'OK' for v in live['clusters'].values()): raise RuntimeError('ClusterSet health changed since planning.')\n"
    if plan["operation"] == "toggle":
        code += "s.set_primary_cluster(p['targetCluster'], {'timeout':p['timeout']})\n"
    else:
        code += "if c.name != p['currentPrimaryCluster']: raise RuntimeError('Connected cluster is no longer the primary cluster.')\n"
        code += "if live['clusters'][c.name].get('primary') != p['currentPrimaryInstance']: raise RuntimeError('Primary member changed since planning; no election submitted.')\n"
        code += "target=live['clusters'][c.name]['topology'].get(p['targetInstance'], {})\n"
        code += "if target.get('status') != 'ONLINE' or target.get('memberRole') != 'SECONDARY': raise RuntimeError('Selected member is no longer an ONLINE secondary.')\n"
        code += "c.set_primary_instance(p['targetInstance'], {'runningTransactionsTimeout':p['timeout']})\n"
    code += "print('MYSQLK8S_JSON='+json.dumps(s.status({'extended':1})))\n"
    return code


@contextmanager
def operation_lock():
    # Share one lock between 31 and 32 on this administration host/workspace.
    directory = Path(__file__).resolve().parents[1] / "generated"
    directory.mkdir(mode=0o700, exist_ok=True)
    identity = os.environ["KUBE_CONTEXT"] + json.dumps(endpoints(), sort_keys=True)
    path = directory / ("operations-" + hashlib.sha256(identity.encode()).hexdigest()[:16] + ".lock")
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise cs.SetupError("Another 31/32 operation is running from this workspace.")
        yield


def switched(status, plan):
    if status.get("status") != "HEALTHY":
        return False
    if plan["operation"] == "toggle":
        old = status.get("clusters", {}).get(plan["currentPrimaryCluster"], {})
        new = status.get("clusters", {}).get(plan["targetCluster"], {})
        return (status.get("primaryCluster") == plan["targetCluster"] and new.get("clusterRole") == "PRIMARY"
                and old.get("clusterRole") == "REPLICA" and all(v.get("globalStatus") == "OK" for v in status.get("clusters", {}).values()))
    return (status.get("primaryCluster") == plan["currentPrimaryCluster"] and
            status.get("clusters", {}).get(plan["currentPrimaryCluster"], {}).get("primary") == plan["targetInstance"])


def execute(operation, args):
    with operation_lock():
        pair, status = load_pair(allow_third=operation == "toggle")
        plan = plan_operation(operation, pair, status, args.timeout, target=getattr(args, "target", None))
        print(json.dumps(plan, indent=2), flush=True)
        if args.dry_run:
            print("Preview only: topology read; no AdminAPI change submitted.")
            return
        if operation == "toggle" and plan["targetCluster"] == plan["currentPrimaryCluster"]:
            print("Selected cluster is already primary; no change submitted.")
            return
        # ClusterSet AdminAPI accepts a session on any member, including a c1
        # replica while c2's cluster3 is primary. DNS/routing supplies the remote
        # MySQL path, so switching back does not require changing kubeconfig.
        source = pair.get(plan["currentPrimaryCluster"], next(iter(pair.values())))
        print("Submitting one planned AdminAPI switchover; waiting for completion...", flush=True)
        # Never retry this mutation: a timeout can mean its outcome is unknown.
        result = run_shell(source["namespace"], source["name"] + "-0", source["data"],
                           mutation_code(plan), timeout=args.timeout + 180)
        deadline = time.monotonic() + args.timeout
        while not switched(result, plan):
            cs.require(time.monotonic() < deadline, "Switchover verification timed out; inspect step 33 before running a toggle again.")
            time.sleep(3)
            result = cs.admin_status(source["namespace"], source["name"], source["data"])["clusterSet"]
        print("Verified: " + json.dumps({"primaryCluster": result["primaryCluster"],
              "primaryInstance": result["clusters"][result["primaryCluster"]]["primary"], "status": result["status"]}), flush=True)
        print("Run ./33-replication-channel-status.sh for c1 channel sources. For c2, inspect ClusterSet status through MySQL Shell.")


SNAPSHOT_CODE = '''
def rows(sql):
    result=shell.get_session().run_sql(sql)
    names=result.get_column_names()
    return [dict(zip(names,row)) for row in result.fetch_all()]
def optional(sql):
    try: return rows(sql)
    except Exception as error: return {'error':str(error)}
keys=('Channel_Name','Source_Host','Source_Port','Source_UUID','Replica_IO_Running','Replica_SQL_Running',
      'Seconds_Behind_Source','Replica_IO_State','Last_IO_Errno','Last_IO_Error','Last_SQL_Errno','Last_SQL_Error')
channels=[{key:row.get(key) for key in keys} for row in rows('SHOW REPLICA STATUS')]
result={'self':rows('SELECT @@hostname AS hostname, @@server_uuid AS server_uuid, @@global.super_read_only AS super_read_only')[0],
        'members':rows('SELECT MEMBER_ID, MEMBER_HOST, MEMBER_PORT, MEMBER_STATE, MEMBER_ROLE FROM performance_schema.replication_group_members'),
        'channels':channels,
        'connections':optional('SELECT CHANNEL_NAME, SOURCE_UUID, SERVICE_STATE, LAST_ERROR_NUMBER, LAST_ERROR_MESSAGE FROM performance_schema.replication_connection_status'),
        'failoverSources':optional('SELECT CHANNEL_NAME, HOST, PORT, WEIGHT, MANAGED_NAME FROM performance_schema.replication_asynchronous_connection_failover')}
print('MYSQLK8S_JSON='+json.dumps(result))
'''


def channel_snapshot():
    report = {"time": datetime.now(timezone.utc).isoformat(), "clusters": [], "errors": []}
    topology = None
    for canonical_name, (namespace, name) in endpoints().items():
        entry = {"cluster": canonical_name, "namespace": namespace, "members": []}
        report["clusters"].append(entry)
        try:
            cluster = cs.get("innodbcluster", name, namespace)
            data = cs.credentials(cluster)
            for ordinal in range(cluster["spec"]["instances"]):
                pod = f"{name}-{ordinal}"
                try:
                    details = run_shell(namespace, pod, data, SNAPSHOT_CODE)
                    details["pod"] = pod
                    entry["members"].append(details)
                    if topology is None:
                        try:
                            # Any available member can retrieve ClusterSet metadata.
                            code = "print('MYSQLK8S_JSON='+json.dumps(dba.get_cluster().get_cluster_set().status({'extended':1})))"
                            topology = run_shell(namespace, pod, data, code)
                        except cs.SetupError as error:
                            report["errors"].append(str(error))
                except (cs.SetupError, subprocess.TimeoutExpired) as error:
                    report["errors"].append(f"{namespace}/{pod}: {error}")
        except (cs.SetupError, subprocess.TimeoutExpired) as error:
            report["errors"].append(f"{namespace}/{name}: {error}")
    report["clusterSet"] = topology
    return report


def source_match(channel, connection, uuid_map, expected):
    # SOURCE_UUID from the receiver is authoritative; a configured host alone
    # does not prove the receiver has reconnected to that server.
    uuid = (connection or {}).get("SOURCE_UUID") or channel.get("Source_UUID")
    endpoint = uuid_map.get(uuid)
    running = channel.get("Replica_IO_Running") == "Yes" and (connection or {}).get("SERVICE_STATE", "ON") == "ON"
    if not running:
        return "NOT CONNECTED", endpoint
    if not endpoint or not expected:
        return "UNKNOWN", endpoint
    return ("MATCH" if endpoint == expected else "DIFFERENT SOURCE"), endpoint


def print_channels(report):
    topology = report.get("clusterSet") or {}
    primary = topology.get("primaryCluster")
    expected = topology.get("clusters", {}).get(primary, {}).get("primary")
    print(f"\n{report['time']} | ClusterSet={topology.get('domainName', '?')} | PRIMARY={primary or '?'}", flush=True)
    print(f"Expected current primary member: {expected or 'unknown'}", flush=True)
    uuid_map = {}
    for cluster in report["clusters"]:
        for member in cluster["members"]:
            for node in member["members"]:
                uuid_map[node["MEMBER_ID"]] = f"{node['MEMBER_HOST']}:{node['MEMBER_PORT']}"
    for cluster in report["clusters"]:
        role = topology.get("clusters", {}).get(cluster["cluster"], {}).get("clusterRole", "UNKNOWN")
        print(f"\n{cluster['namespace']}/{cluster['cluster']} ({role})")
        for member in cluster["members"]:
            group_role = next((m["MEMBER_ROLE"] for m in member["members"] if m["MEMBER_ID"] == member["self"]["server_uuid"]), "?")
            print(f"  {member['pod']} | group role={group_role} | super_read_only={member['self']['super_read_only']}")
            channels = [c for c in member["channels"] if not str(c.get("Channel_Name", "")).startswith("group_replication")]
            connections = member.get("connections")
            connection_map = {r["CHANNEL_NAME"]: r for r in connections} if isinstance(connections, list) else {}
            if not channels:
                print("    No asynchronous replication channel (normal on the ClusterSet primary).")
            for channel in channels:
                connection = connection_map.get(channel["Channel_Name"])
                match, actual = source_match(channel, connection, uuid_map, expected)
                connected_uuid = (connection or {}).get("SOURCE_UUID") or channel["Source_UUID"]
                lag = "unknown" if channel["Seconds_Behind_Source"] is None else str(channel["Seconds_Behind_Source"]) + "s"
                print(f"    channel={channel['Channel_Name']} configured source={channel['Source_Host']}:{channel['Source_Port']}")
                print(f"    IO={channel['Replica_IO_Running']} SQL={channel['Replica_SQL_Running']} lag={lag} | {match}")
                if role == "REPLICA" and group_role == "SECONDARY" and match == "NOT CONNECTED":
                    print("    Standby channel on a group SECONDARY; a stopped receiver is expected here.")
                print(f"    Receiver source UUID={connected_uuid} -> {actual or 'unresolved'}")
                for key in ("Last_IO_Error", "Last_SQL_Error"):
                    if channel.get(key): print(f"    {key}: {channel[key]}")
            sources = member.get("failoverSources")
            if isinstance(sources, list):
                for candidate in sources:
                    print(f"    Failover candidate: {candidate['CHANNEL_NAME']} {candidate['HOST']}:{candidate['PORT']} weight={candidate['WEIGHT']}")
    for error in report["errors"]:
        print("  WARNING: " + error, file=sys.stderr)
    print(flush=True)


def main():
    parser = argparse.ArgumentParser(description="ClusterSet planned operations and channel monitoring.")
    parser.add_argument("operation", choices=("toggle", "rotate", "channels"))
    parser.add_argument("target", nargs="?", choices=("1", "2", "3"),
                        help="Step 31: select 1=mycluster, 2=mycluster2, 3=mycluster3. Omit to cycle through registered targets.")
    parser.add_argument("--dry-run", action="store_true", help="Preview 31/32 selection without changes.")
    parser.add_argument("--timeout", type=int, default=120, help="Switchover synchronization/transaction wait in seconds (default: 120).")
    parser.add_argument("--watch", action="store_true", help="Repeat read-only channel snapshots until Ctrl-C.")
    parser.add_argument("--interval", type=int, default=5, help="Seconds between completed snapshots (default: 5).")
    parser.add_argument("--json", action="store_true", help="Print complete channel snapshots as JSON (one per watch iteration).")
    args = parser.parse_args()
    cs.require(args.target is None or args.operation == "toggle", "A numeric target applies to step 31 only.")
    cs.settings()
    cs.require(1 <= args.timeout <= 3600 and 1 <= args.interval <= 3600, "Timeout and interval must be from 1 to 3600 seconds.")
    if args.operation == "channels":
        cs.require(not args.dry_run, "Step 33 is already read-only; use --watch or --json.")
        while True:
            report = channel_snapshot()
            if args.json: print(json.dumps(report, default=str), flush=True)
            else: print_channels(report)
            if not args.watch:
                cs.require(any(c["members"] for c in report["clusters"]), "No database members could be queried.")
                break
            time.sleep(args.interval)
    else:
        cs.require(not args.watch and not args.json, "--watch/--json apply to step 33 only.")
        execute(args.operation, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (cs.SetupError, subprocess.TimeoutExpired) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        if len(sys.argv) > 1 and sys.argv[1] != "channels":
            print("If a change was submitted, its outcome may be unknown. Inspect step 33 before retrying; no automatic rollback or retry was attempted.", file=sys.stderr)
        sys.exit(1)
