#!/usr/bin/env python3
"""Native operator ClusterSet bootstrap; passwords stay in memory/stdin."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import cluster_config

ROOT = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise SetupError(message)


def kubectl(*args, payload=None, json_output=False, timeout=45):
    command = ["kubectl", "--context", os.environ["KUBE_CONTEXT"],
               "--request-timeout=30s", *args]
    result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise SetupError(result.stderr.strip() or result.stdout.strip() or "kubectl failed")
    if json_output:
        return json.loads(result.stdout) if result.stdout.strip() else None
    return result.stdout.strip()


def get(kind, name=None, namespace=None, optional=False):
    args = (["-n", namespace] if namespace else []) + ["get", kind]
    if name:
        args.append(name)
    if optional:
        args.append("--ignore-not-found")
    return kubectl(*args, "-o", "json", json_output=True)


def settings():
    keys = ("PRIMARY_MYSQL_NAMESPACE", "PRIMARY_MYSQL_CLUSTER_NAME",
            "REPLICA_MYSQL_NAMESPACE", "REPLICA_MYSQL_CLUSTER_NAME", "REPLICA_MYSQL_SECRET_NAME")
    for key in keys:
        value = os.environ[key]
        require(len(value) <= 40 and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value),
                f"{key} must be a lowercase DNS label starting with a letter, <=40 characters.")
    pn, pc, rn, rc, secret = (os.environ[k] for k in keys)
    require(pn != rn, "The replica must use a different namespace from the primary.")
    require(pc.replace("-", "_") != rc.replace("-", "_"), "ClusterSet member names must be distinct.")
    require(rn not in ("default", "kube-system", "kube-public", "kube-node-lease", os.environ["OPERATOR_NAMESPACE"]),
            "Use a dedicated replica workload namespace.")
    for key, low, high in (("REPLICA_BASE_SERVER_ID", 1, 3999999900),
                           ("REPLICA_MYSQL_INSTANCES", 1, 9), ("REPLICA_MYSQL_ROUTER_INSTANCES", 1, 9)):
        value = os.environ[key]
        require(value.isdigit() and low <= int(value) <= high, f"Invalid {key}.")
    require(re.fullmatch(r"[1-9][0-9]*(Mi|Gi|Ti)", os.environ["REPLICA_STORAGE_SIZE"]),
            "REPLICA_STORAGE_SIZE must be a positive Mi/Gi/Ti quantity.")
    require(re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", os.environ["K8S_CLUSTER_DOMAIN"]),
            "Invalid K8S_CLUSTER_DOMAIN.")
    return pn, pc, rn, rc, secret


def credentials(primary):
    secret = get("secret", primary["spec"]["secretName"], primary["metadata"]["namespace"])
    data = secret.get("data", {})
    require(all(data.get(k) for k in ("rootUser", "rootHost", "rootPassword")), "Primary Secret is incomplete.")
    require(base64.b64decode(data["rootUser"]) == b"root" and base64.b64decode(data["rootHost"]) == b"%",
            "These scripts require rootUser=root and rootHost=% in the primary Secret.")
    require(not any(c in base64.b64decode(data["rootPassword"]) for c in (b"\n", b"\r", b"\0")),
            "Password contains characters unsupported by MySQL Shell password stdin.")
    return {key: data[key] for key in ("rootUser", "rootHost", "rootPassword")}


def admin_status(namespace, name, data):
    # The connection is local to a member's sidecar; AdminAPI discovers the
    # current primary. No assumption that pod 0 remains the primary is needed.
    code = ('import json; c=dba.get_cluster(); cs=c.get_cluster_set(); '
            'print("MYSQLK8S_JSON="+json.dumps({"clusterName":c.name,"clusterSet":cs.status({"extended":1}),'
            '"superReadOnly":shell.get_session().run_sql("SELECT @@GLOBAL.super_read_only").fetch_one()[0]}))')
    output = kubectl("--request-timeout=0", "-n", namespace, "exec", "-i", name + "-0", "-c", "sidecar", "--",
                     "mysqlsh", "--uri", "root@127.0.0.1:3306", "--passwords-from-stdin", "--py",
                     "--quiet-start=2", "-e", code,
                     payload=base64.b64decode(data["rootPassword"]).decode() + "\n", timeout=90)
    marker = "MYSQLK8S_JSON="
    require(marker in output, "MySQL Shell did not return ClusterSet status.")
    return json.JSONDecoder().raw_decode(output.split(marker, 1)[1])[0]


def primary_role(status, expected):
    cs = status["clusterSet"]
    require(status["clusterName"] == expected and cs.get("primaryCluster") == expected,
            "The selected primary is not the current ClusterSet primary. No roles were changed.")
    require(cs.get("clusters", {}).get(expected, {}).get("globalStatus") == "OK",
            "Primary ClusterSet member is not globally healthy.")


def validate_existing(existing, manifest):
    expected = manifest["spec"]
    if existing is None:
        return
    actual = existing["spec"]
    for key in ("initDB", "baseServerId", "secretName", "version", "instances", "tlsUseSelfSigned"):
        require(actual.get(key) == expected.get(key),
                f"Existing replica {key} differs from the requested bootstrap. Refusing to modify an existing cluster.")
    require(actual.get("router", {}).get("instances") == expected["router"]["instances"],
            "Existing replica Router count differs; manage scaling separately.")
    require(actual.get("datadirVolumeClaimTemplate") == expected["datadirVolumeClaimTemplate"],
            "Existing replica storage differs; manage volume changes separately.")


def build_replica(primary, storage_class):
    pn, pc, rn, rc, secret = settings()
    saved = dict(os.environ)
    try:
        os.environ.update({"MYSQL_NAMESPACE": rn, "MYSQL_CLUSTER_NAME": rc, "MYSQL_SECRET_NAME": secret,
                           "MYSQL_INSTANCES": os.environ["REPLICA_MYSQL_INSTANCES"],
                           "MYSQL_ROUTER_INSTANCES": os.environ["REPLICA_MYSQL_ROUTER_INSTANCES"],
                           "MYSQL_VERSION": primary["spec"]["version"], "STORAGE_CLASS": storage_class,
                           "MYSQL_STORAGE_SIZE": os.environ["REPLICA_STORAGE_SIZE"]})
        cluster_config.validate()
        doc = cluster_config.build_manifest()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    doc["spec"]["edition"] = primary["spec"].get("edition", "community")
    doc["spec"]["baseServerId"] = int(os.environ["REPLICA_BASE_SERVER_ID"])
    target = f"{pc}-0.{pc}-instances.{pn}.svc.{os.environ['K8S_CLUSTER_DOMAIN']}:3306"
    doc["spec"]["initDB"] = {"clusterSet": {"targetUrl": target, "rootUser": "root", "secretKeyRef": {"name": secret}}}
    return doc


def check_workers(nodes):
    ready = []
    for node in nodes["items"]:
        spec = node.get("spec", {})
        labels = node["metadata"].get("labels", {})
        if (not spec.get("unschedulable")
                and not any(t["effect"] in ("NoSchedule", "NoExecute") for t in spec.get("taints", []))
                and labels.get("kubernetes.io/os") == "linux"
                and labels.get("kubernetes.io/arch") in ("amd64", "arm64")
                and any(c["type"] == "Ready" and c["status"] == "True" for c in node.get("status", {}).get("conditions", []))):
            ready.append(node["metadata"]["name"])
    needed = int(os.environ["REPLICA_MYSQL_INSTANCES"]) if os.environ["MYSQL_ANTI_AFFINITY"] == "required" else 1
    require(len(ready) >= needed, f"Need {needed} eligible Ready workers for replica placement; found {len(ready)}.")
    print("Eligible workers for the additional cluster: " + ", ".join(ready), flush=True)


def check():
    pn, pc, rn, rc, secret = settings()
    primary = get("innodbcluster", pc, pn)
    health = primary.get("status", {}).get("cluster", {})
    require(health.get("status") == "ONLINE" and health.get("onlineInstances") == primary["spec"]["instances"],
            "Primary cluster must have all configured instances ONLINE.")
    require(primary["spec"].get("tlsUseSelfSigned") is True,
            "These starter scripts support self-signed TLS; configure replica TLS Secrets separately for managed certificates.")
    crd = get("crd", "innodbclusters.mysql.oracle.com")
    schema = next(v["schema"]["openAPIV3Schema"] for v in crd["spec"]["versions"] if v["name"] == "v2")
    require("clusterSet" in schema["properties"]["spec"]["properties"]["initDB"]["properties"],
            "Installed operator CRD lacks native initDB.clusterSet support.")
    deployment = get("deployment", "mysql-operator", os.environ["OPERATOR_NAMESPACE"])
    require(deployment.get("status", {}).get("availableReplicas", 0) >= 1, "MySQL Operator is not available.")
    env = {e["name"]: e.get("value", "") for e in deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    scopes = [s.strip() for s in env.get("OPERATOR_NAMESPACES", "").split(",") if s.strip()]
    require(not scopes or pn in scopes and rn in scopes, "The installed operator does not watch both namespaces.")
    check_workers(get("nodes"))
    storage = os.environ["REPLICA_STORAGE_CLASS"] or primary["spec"]["datadirVolumeClaimTemplate"].get("storageClassName")
    require(storage, "Set REPLICA_STORAGE_CLASS; the primary has no explicit StorageClass.")
    get("storageclass", storage)
    doc = build_replica(primary, storage)
    # Server IDs must be unique across all ClusterSet instances. Reserve the
    # operator's full 100-ID block for each main cluster, including later growth.
    clusters = json.loads(kubectl("get", "innodbclusters", "-A", "-o", "json"))["items"]
    base = doc["spec"]["baseServerId"]
    for other in clusters:
        if (other["metadata"]["namespace"], other["metadata"]["name"]) == (rn, rc):
            continue
        ranges = [int(other["spec"].get("baseServerId", 1000))]
        ranges += [int(r["baseServerId"]) for r in other["spec"].get("readReplicas", [])]
        require(all(base + 99 < start or start + 99 < base for start in ranges),
                f"Replica server-ID range overlaps {other['metadata']['namespace']}/{other['metadata']['name']}.")
    existing = get("innodbcluster", rc, rn, optional=True) if get("namespace", rn, optional=True) else None
    validate_existing(existing, doc)
    data = credentials(primary)
    status = admin_status(pn, pc, data)
    primary_role(status, pc.replace("-", "_"))
    # Check DNS from the primary sidecar, without creating diagnostic pods.
    dns_code = 'import socket; print(socket.gethostbyname("kubernetes.default.svc.' + os.environ["K8S_CLUSTER_DOMAIN"] + '"))'
    kubectl("-n", pn, "exec", pc + "-0", "-c", "sidecar", "--", "mysqlsh", "--py", "-e", dns_code)
    print(f"Primary: {pn}/{pc}; ClusterSet: {status['clusterSet']['domainName']}", flush=True)
    print(f"Replica: {rn}/{rc}; {doc['spec']['instances']} MySQL pods, {doc['spec']['router']['instances']} Routers, "
          f"{os.environ['REPLICA_STORAGE_SIZE']} per PVC; baseServerId={base}", flush=True)
    print("Allow cross-namespace DNS and TCP/3306 between all members. Replica bootstrap clones the primary's data.", flush=True)
    return primary, doc, data, existing


def create(verify_script="13-verify-clusterset.sh"):
    primary, doc, data, existing = check()
    pn, pc, rn, rc, secret = settings()
    if existing:
        saved_secret = get("secret", secret, rn, optional=True)
        require(saved_secret and saved_secret.get("data") == data,
                "Replica exists but its original credentials are missing or differ. Restore them before continuing.")
        print(f"Replica CR already matches. Run {verify_script}; no resources changed.")
        return
    if not get("namespace", rn, optional=True):
        kubectl("create", "namespace", rn)
    # Refuse reusing volumes from a removed or independently created cluster.
    pvcs = get("pvc", namespace=rn)["items"]
    require(not any(p["metadata"]["name"].startswith("datadir-" + rc + "-") for p in pvcs),
            "Replica datadir PVCs already exist without a matching cluster. Use a fresh name/namespace.")
    require(get("statefulset", rc, rn, optional=True) is None, "Orphan replica StatefulSet exists; use a fresh name.")
    rendered = json.dumps(doc, indent=2) + "\n"
    kubectl("-n", rn, "apply", "--dry-run=server", "-f", "-", payload=rendered)
    dest_secret = get("secret", secret, rn, optional=True)
    if dest_secret:
        require(dest_secret.get("data") == data, "Existing replica Secret differs from primary credentials; refusing to overwrite.")
    else:
        copied = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": secret, "namespace": rn},
                  "type": "Opaque", "data": data}
        # JSON goes through stdin, never argv, terminal output, or a local file.
        kubectl("create", "-f", "-", payload=json.dumps(copied))
    generated = ROOT / "generated"
    generated.mkdir(mode=0o700, exist_ok=True)
    path = generated / "replica-innodbcluster.json"
    path.write_text(rendered)
    path.chmod(0o600)
    kubectl("create", "-f", "-", payload=rendered)
    print(f"Replica bootstrap submitted. Manifest: {path}. Run {verify_script}.")


def diagnostics():
    pn, pc, rn, rc, _ = settings()
    for ns in (pn, rn):
        for args in (("get", "innodbclusters,pods,pvc,services", "-o", "wide"),
                     ("get", "events", "--sort-by=.lastTimestamp")):
            try:
                print(kubectl("-n", ns, *args)[-12000:])
            except (SetupError, subprocess.TimeoutExpired) as error:
                print(f"Diagnostics: {error}", file=sys.stderr)


def verify():
    pn, pc, rn, rc, _ = settings()
    deadline = time.monotonic() + int(os.environ["WAIT_TIMEOUT_SECONDS"])
    expected_count = int(os.environ["REPLICA_MYSQL_INSTANCES"])
    while True:
        replica = get("innodbcluster", rc, rn)
        require(replica["spec"]["instances"] == expected_count, "Configured replica instance count differs from deployed spec.")
        health = replica.get("status", {}).get("cluster", {})
        print(f"Replica status: {health.get('status', 'PENDING')}; online={health.get('onlineInstances', 0)}/{expected_count}", flush=True)
        if health.get("status") == "ONLINE" and health.get("onlineInstances") == expected_count:
            break
        require(time.monotonic() < deadline, "Timed out waiting for replica cluster readiness.")
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    for resource in ("statefulset/" + rc, "deployment/" + rc + "-router"):
        kubectl("--request-timeout=0", "-n", rn, "rollout", "status", resource,
                "--timeout=" + os.environ["WAIT_TIMEOUT_SECONDS"] + "s",
                timeout=int(os.environ["WAIT_TIMEOUT_SECONDS"]) + 30)
    primary = get("innodbcluster", pc, pn)
    data = credentials(primary)
    status = admin_status(pn, pc, data)
    primary_role(status, pc.replace("-", "_"))
    cs = status["clusterSet"]
    member = cs.get("clusters", {}).get(rc.replace("-", "_"), {})
    require(member.get("clusterRole") == "REPLICA" and member.get("globalStatus") == "OK",
            "Replica is not a healthy ClusterSet REPLICA: " + json.dumps(member))
    require(cs.get("status") == "HEALTHY", "ClusterSet is not HEALTHY: " + json.dumps(cs))
    local = admin_status(rn, rc, data)
    require(local["clusterSet"].get("domainName") == cs["domainName"] and local["superReadOnly"] == 1,
            "Replica does not report the same ClusterSet with super_read_only enabled.")
    print(json.dumps(cs, indent=2))
    print(f"Verified PRIMARY={pn}/{pc}, REPLICA={rn}/{rc}; ClusterSet HEALTHY, replica read-only.")
    print("ClusterSet replication is asynchronous. Namespace separation within one Kubernetes cluster is not site-level DR.")


if __name__ == "__main__":
    try:
        settings()
        {"check": check, "create": create, "verify": verify}[sys.argv[1]]()
    except (SetupError, subprocess.TimeoutExpired) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        if sys.argv[1] == "verify":
            diagnostics()
        sys.exit(1)
