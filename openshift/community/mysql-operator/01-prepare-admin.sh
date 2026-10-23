#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
umask 077
mkdir -p "$MYSQL_STATE_DIR/tools" "$(dirname -- "$MYSQL_REGIONAL_CONFIG")/bin" "$MYSQL_STATE_DIR/downloads"
if [[ ! -x $MYSQL_OCI_PYTHON ]] || ! "$MYSQL_OCI_PYTHON" -c 'import oci,yaml' >/dev/null 2>&1; then
 python3 -m venv "$MYSQL_STATE_DIR/tools"
 "$MYSQL_OCI_PYTHON" -m pip install 'oci==2.187.0' 'PyYAML==6.0.3'
fi
bin_dir="$(dirname -- "$MYSQL_REGIONAL_CONFIG")/bin"
if [[ ! -x $bin_dir/helm ]]; then
 [[ $(uname -m) == x86_64 ]] || { echo 'This lab selects amd64.' >&2; exit 1; }
 archive="$MYSQL_STATE_DIR/downloads/helm.tar.gz"
 curl -fLsS --retry 3 "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" -o "$archive"
 printf '%s  %s\n' '0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b' "$archive" | sha256sum -c -
 tar -xzf "$archive" -C "$MYSQL_STATE_DIR/downloads" linux-amd64/helm
 install -m 700 "$MYSQL_STATE_DIR/downloads/linux-amd64/helm" "$bin_dir/helm"
fi
[[ $(helm version --template '{{.Version}}') == "$HELM_VERSION" ]] || exit 1
chart="$MYSQL_STATE_DIR/downloads/mysql-operator-$OPERATOR_CHART_VERSION.tgz"
curl -fLsS --retry 3 "https://mysql.github.io/mysql-operator/mysql-operator-$OPERATOR_CHART_VERSION.tgz" -o "$chart"
printf '%s  %s\n' '644906374a03c911332da5b48f23d8195cd05ec776a9e56a304e3b030f072a56' "$chart" | sha256sum -c -
echo 'Private administration tools and pinned chart ready; no cluster resources changed.'
