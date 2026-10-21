#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then echo 'Usage: ./07-deploy-demo.sh (creates/updates only hello-demo resources)'; exit 0; fi
load_config; load_host
remote_run "$ADMIN_WORKDIR" "$KUBECONFIG" <<'REMOTE'
set -euo pipefail
source "$1/env.sh"
export KUBECONFIG=$2
[[ -r "$KUBECONFIG" ]] || { echo "Configured KUBECONFIG is unavailable: $KUBECONFIG" >&2; exit 1; }
expected_server="https://api.$(cat "$ADMIN_WORKDIR/cluster-domain"):6443"
actual_server=$(oc config view --minify -o jsonpath='{.clusters[0].cluster.server}')
[[ $actual_server == "$expected_server" ]] || {
  echo "KUBECONFIG selects $actual_server; expected $expected_server. Stop and correct the environment." >&2; exit 1;
}
oc get namespace hello-demo >/dev/null 2>&1 || oc create namespace hello-demo
oc -n hello-demo create deployment hello --image=quay.io/openshifttest/hello-openshift@sha256:cc6eafafab7a31c18ef22a9199220fee0615b501f2e1923cd881784243b76aea \
  --dry-run=client -o yaml | oc apply -f -
oc -n hello-demo set resources deployment/hello --requests=cpu=100m,memory=128Mi --limits=cpu=500m,memory=256Mi
oc -n hello-demo expose deployment hello --port=8080 --target-port=8080 --dry-run=client -o yaml | oc apply -f -
oc -n hello-demo create route edge hello --service=hello --port=8080 --insecure-policy=Redirect \
  --dry-run=client -o yaml | oc apply -f -
oc -n hello-demo rollout status deployment/hello --timeout=5m
oc -n hello-demo get deployment,pods,service,route
oc -n hello-demo logs deployment/hello --tail=20
printf '\nDemo available through the route from a client with private routing, DNS and trusted ingress CA.\n'
REMOTE
