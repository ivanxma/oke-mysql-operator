#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --help || ${1:-} == -h ]]; then echo 'Usage: ./05-install-mysql-operator.sh (dedicated namespace/SCC setup and official Helm chart)'; exit 0; fi
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
connect_kubernetes; need helm
worker_check
h repo add mysql-operator https://mysql.github.io/mysql-operator/ --force-update
h repo update mysql-operator
umask 077
mkdir -p "$MYSQL_STATE_DIR/generated"
values="$MYSQL_STATE_DIR/generated/operator-values.json"
python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" operator-values > "$values"
releases=$(h list -n "$OPERATOR_NAMESPACE" --deployed --failed --pending --uninstalled --superseded --uninstalling --filter '^mysql-operator$' -o json)
state=$(printf '%s' "$releases" | python3 -c 'import sys,json; r=json.load(sys.stdin); print((r[0]["chart"]+" "+r[0]["status"]) if r else "absent")')
[[ $state == absent || $state == "mysql-operator-$OPERATOR_CHART_VERSION deployed" ]] || die "Resolve existing release explicitly: $state"
if [[ $state == absent ]]; then
  python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" security
  h install mysql-operator mysql-operator/mysql-operator --version "$OPERATOR_CHART_VERSION" \
    --namespace "$OPERATOR_NAMESPACE" --values "$values" --wait --timeout "${WAIT_TIMEOUT_SECONDS}s"
else
  h get values mysql-operator -n "$OPERATOR_NAMESPACE" -o json > "$MYSQL_STATE_DIR/generated/live-operator-values.json"
  python3 - "$values" "$MYSQL_STATE_DIR/generated/live-operator-values.json" <<'PY'
import json,sys
expected,actual=[json.load(open(p)) for p in sys.argv[1:]]
if any(actual.get(k)!=v for k,v in expected.items()):
 raise SystemExit('Existing operator scope/placement/resources differ; no automatic upgrade or migration is performed.')
PY
  python3 "$MYSQL_OPENSHIFT_DIR/lib/runtime.py" security
  log 'Reusing the matching deployed operator.'
fi
k wait --for=condition=Established crd/innodbclusters.mysql.oracle.com --timeout=120s
k --request-timeout=0 -n "$OPERATOR_NAMESPACE" rollout status deployment/mysql-operator --timeout="${WAIT_TIMEOUT_SECONDS}s"
log 'Operator watches both database namespaces and is scheduled on the labelled workers.'
