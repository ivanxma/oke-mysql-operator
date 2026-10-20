#!/usr/bin/env python3
"""Reject a kubeconfig targeting a different OCI cluster before replacing config2."""
import json
import sys


def validate(config, cluster_id, context_name):
    contexts = config.get("contexts", [])
    if len(contexts) != 1 or contexts[0]["name"] != context_name:
        raise ValueError("config2 must contain exactly one context named c2.")
    if config.get("current-context") != context_name:
        raise ValueError("config2 current context must be c2.")
    user_name = contexts[0]["context"]["user"]
    user = next((u["user"] for u in config.get("users", []) if u["name"] == user_name), {})
    args = user.get("exec", {}).get("args", [])
    command = user.get("exec", {}).get("command", "")
    if command.rsplit("/", 1)[-1] != "oci" or "--cluster-id" not in args:
        raise ValueError("Expected an OCI exec authentication entry with --cluster-id.")
    pos = args.index("--cluster-id")
    if pos + 1 >= len(args) or args[pos + 1] != cluster_id:
        raise ValueError("config2 targets a different OKE cluster; refusing to replace it.")
    cluster_name = contexts[0]["context"]["cluster"]
    cluster = next((c["cluster"] for c in config.get("clusters", []) if c["name"] == cluster_name), {})
    if not cluster.get("server", "").startswith("https://") or not cluster.get("certificate-authority-data"):
        raise ValueError("Expected an HTTPS API endpoint and embedded cluster CA.")


if __name__ == "__main__":
    try:
        validate(json.load(sys.stdin), sys.argv[1], sys.argv[2])
    except (ValueError, KeyError, TypeError) as error:
        sys.exit("ERROR: " + str(error))
