#!/usr/bin/env bash
# Validate console and optionally demo HTTPS through admin-host private DNS.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./11-verify-web-access.sh [--include-demo]'; exit 0
fi
demo=false
if [[ ${1:-} == --include-demo ]]; then demo=true; shift; fi
[[ $# == 0 ]] || fail 'Use --include-demo or no arguments.'
load_config; load_host
remote_run "$ADMIN_WORKDIR" "$KUBECONFIG" "$demo" "${INGRESS_CA_FILE:-}" <<'REMOTE'
set -euo pipefail
work=$1 selected=$2 demo=$3 ca=$4
source "$work/env.sh"; export KUBECONFIG=$selected
[[ -r $KUBECONFIG ]] || { echo 'Configured KUBECONFIG is unavailable.' >&2; exit 1; }
[[ $(oc config view --minify -o jsonpath='{.clusters[0].cluster.server}') == "https://api.$(cat "$work/cluster-domain"):6443" ]] || { echo 'Wrong cluster selected.' >&2; exit 1; }
umask 077
mkdir -p "$work/validation"
if [[ -z $ca ]]; then
  ca="$work/validation/ingress-ca.crt"
  oc -n openshift-ingress-operator get secret router-ca -o jsonpath='{.data.tls\.crt}' | base64 -d > "$ca"
fi
[[ -s $ca ]] || { echo 'Ingress CA unavailable; set INGRESS_CA_FILE for a custom certificate chain.' >&2; exit 1; }
check_route() {
  local namespace=$1 name=$2 host code
  host=$(oc -n "$namespace" get route "$name" -o jsonpath='{.spec.host}')
  code=$(curl --fail --silent --show-error --max-time 30 --cacert "$ca" \
    --output "$work/validation/$name.html" --write-out '%{http_code}' "https://$host")
  [[ $code == 200 ]] || { echo "FAIL: $name returned HTTP $code" >&2; return 1; }
  printf 'PASS: %s HTTPS 200 with certificate verification\n' "$name"
}
check_route openshift-console console
if $demo; then check_route hello-demo hello; fi
REMOTE
