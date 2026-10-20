#!/usr/bin/env python3
"""Build an unprivileged, temporary Shell pod from a live database sidecar image."""
import json
import sys


def build_pod(database_pod, namespace, name):
    spec = database_pod["spec"]
    sidecar = next((c for c in spec["containers"] if c["name"] == "sidecar"), None)
    if not sidecar:
        raise ValueError("Selected database pod has no MySQL Operator sidecar image.")
    result = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": name, "namespace": namespace,
                     "labels": {"app.kubernetes.io/name": "mysqlsh-client"}},
        "spec": {
            "restartPolicy": "Never", "dnsPolicy": "ClusterFirst", "hostNetwork": False,
            "automountServiceAccountToken": False,
            "nodeSelector": {"kubernetes.io/os": "linux"},
            "securityContext": {"runAsNonRoot": True, "runAsUser": 2, "runAsGroup": 2, "fsGroup": 2},
            "terminationGracePeriodSeconds": 5,
            "containers": [{
                "name": "mysqlsh", "image": sidecar["image"], "imagePullPolicy": "IfNotPresent",
                "command": ["/bin/sh", "-c", "mkdir -p /tmp/mysqlsh && exec sleep 86400"],
                "env": [{"name": "MYSQLSH_USER_CONFIG_HOME", "value": "/tmp/mysqlsh"},
                        {"name": "MYSQLSH_CREDENTIAL_STORE_SAVE_PASSWORDS", "value": "never"}],
                "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}},
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}],
            "volumes": [{"name": "tmp", "emptyDir": {}}]}}
    if spec.get("imagePullSecrets"):
        result["spec"]["imagePullSecrets"] = spec["imagePullSecrets"]
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(build_pod(json.load(sys.stdin), sys.argv[1], sys.argv[2]), indent=2))
    except (ValueError, KeyError) as error:
        sys.exit("ERROR: " + str(error))
