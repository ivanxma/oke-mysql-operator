#!/usr/bin/env bash
# Single-member lab primary recovery after complete shutdown; preserves data.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
case ${1:-} in
 '') step recovery-preview ;;
 --apply) [[ $# -eq 1 ]] || exit 1; step recovery-apply ;;
 *) echo 'Usage: 12-recover-primary.sh [--apply]' >&2; exit 1 ;;
esac
