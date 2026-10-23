#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
step validate
step security
umask 077
mkdir -p "$MYSQLK8S_GENERATED_DIR"
values="$MYSQLK8S_GENERATED_DIR/operator-values.json"
step values > "$values"
chart="$MYSQL_STATE_DIR/downloads/mysql-operator-$OPERATOR_CHART_VERSION.tgz"
helm_args=(--kubeconfig "$KUBECONFIG" --kube-context "$KUBE_CONTEXT")
status=$(helm "${helm_args[@]}" list -n "$OPERATOR_NAMESPACE" --all --filter '^mysql-operator$' -o json)
if [[ $(printf '%s' "$status" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))') == 0 ]]; then
 helm "${helm_args[@]}" install mysql-operator "$chart" --namespace "$OPERATOR_NAMESPACE" --values "$values" --wait --timeout 600s
else
 printf '%s' "$status" | python3 -c 'import json,sys,os; r=json.load(sys.stdin); assert len(r)==1 and r[0]["chart"]=="mysql-operator-"+os.environ["OPERATOR_CHART_VERSION"] and r[0]["status"]=="deployed", "Existing release requires explicit review"'
 helm "${helm_args[@]}" get values mysql-operator -n "$OPERATOR_NAMESPACE" -o json > "$MYSQLK8S_GENERATED_DIR/live-operator-values.json"
 python3 - "$values" "$MYSQLK8S_GENERATED_DIR/live-operator-values.json" <<'PY'
import json,sys
a,b=[json.load(open(p)) for p in sys.argv[1:]]
if any(b.get(k)!=v for k,v in a.items()):raise SystemExit('Existing operator scope differs; no upgrade/adoption.')
PY
fi
oc --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" wait --for=condition=Established crd/innodbclusters.mysql.oracle.com --timeout=120s
oc --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" -n "$OPERATOR_NAMESPACE" rollout status deployment/mysql-operator --timeout=600s
echo 'Regional operator prepared for its dedicated database namespace.'
