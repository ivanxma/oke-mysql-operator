#!/usr/bin/env bash
set -euo pipefail
set +x
OKE2_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OKE2_CONFIG_FILE="${OKE2_CONFIG:-$OKE2_DIR/config.env}"
if [[ -f "$OKE2_CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$OKE2_CONFIG_FILE"
fi
OCI_REGION="${OCI_REGION:-eu-frankfurt-1}"
OCI_COMPARTMENT_ID="${OCI_COMPARTMENT_ID:-}"
C2_CLUSTER_ID="${C2_CLUSTER_ID:-}"
C2_KUBECONFIG="${C2_KUBECONFIG:-$HOME/.kube/config2}"
C2_KUBE_ENDPOINT="${C2_KUBE_ENDPOINT:-PRIVATE_ENDPOINT}"
C2_CONTEXT=c2
OCI_PROFILE="${OCI_PROFILE:-}"
OCI_AUTH="${OCI_AUTH:-}"
log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
validate_destination() {
    need python3
    # Reject aliases/symlinks to the first cluster's default kubeconfig.
    python3 - "$C2_KUBECONFIG" "$HOME/.kube/config" <<'PY'
from pathlib import Path
import sys
target, primary = map(lambda p: Path(p).expanduser(), sys.argv[1:])
if not target.is_absolute(): sys.exit('ERROR: C2_KUBECONFIG must be an absolute path.')
if target.is_symlink() or target.resolve() == primary.resolve():
    sys.exit('ERROR: config2 must be a separate file, not a symlink or the default ~/.kube/config.')
if target.exists() and primary.exists() and target.samefile(primary):
    sys.exit('ERROR: config2 must not be a hard link to ~/.kube/config.')
PY
}
oci2() {
    local flags=(--region "$OCI_REGION" --connection-timeout 10 --read-timeout 30)
    if [[ -n "$OCI_PROFILE" ]]; then flags+=(--profile "$OCI_PROFILE"); fi
    if [[ -n "$OCI_AUTH" ]]; then flags+=(--auth "$OCI_AUTH"); fi
    oci "${flags[@]}" "$@"
}
k2() { kubectl --kubeconfig "$C2_KUBECONFIG" --context "$C2_CONTEXT" --request-timeout=20s "$@"; }
require_c2_config() {
    validate_destination
    need kubectl
    [[ -s "$C2_KUBECONFIG" ]] || die "Run 02-create-kubeconfig.sh first: $C2_KUBECONFIG is missing."
    [[ $(kubectl --kubeconfig "$C2_KUBECONFIG" config get-contexts "$C2_CONTEXT" -o name) == "$C2_CONTEXT" ]] || die 'Context c2 is missing from config2.'
}
