#!/usr/bin/env bash
# Read-only DNS/TCP prerequisite check from both regional operator pods.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
step discovery-check
