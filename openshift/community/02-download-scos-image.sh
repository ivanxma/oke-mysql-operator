#!/usr/bin/env bash
# Download the matching uncustomized SCOS live ISO before a target IP/VM exists.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: ./02-download-scos-image.sh (after 02-download-okd.sh; no NODE_IP needed)'
  exit 0
fi
[[ $# -eq 0 ]] || fail 'This step takes no arguments; use the private environment.'
load_config preparation; load_host
remote_run "$OKD_VERSION" "$ADMIN_WORKDIR" <<'REMOTE'
set -euo pipefail
version=$1 work=$2
[[ -f $work/downloaded-version && $(cat "$work/downloaded-version") == "$version" ]] || {
  echo 'Run 02-download-okd.sh for the selected release first.' >&2; exit 1;
}
umask 077
cd "$work"
"$work/bin/openshift-install" coreos print-stream-json > coreos-stream.json
iso_url=$(jq -er '.architectures.x86_64.artifacts.metal.formats.iso.disk.location' coreos-stream.json)
iso_sha=$(jq -er '.architectures.x86_64.artifacts.metal.formats.iso.disk.sha256' coreos-stream.json)
[[ $iso_url == https://* && $iso_sha =~ ^[a-fA-F0-9]{64}$ ]] || { echo 'Invalid SCOS ISO metadata.' >&2; exit 1; }
if [[ -f scos-live.iso ]] && printf '%s  %s\n' "$iso_sha" scos-live.iso | sha256sum -c - >/dev/null 2>&1; then
  echo 'Matching checksum-verified base SCOS ISO already present.'
else
  temporary=$(mktemp "$work/.scos-live.XXXXXX")
  trap 'rm -f -- "$temporary"' EXIT
  curl --fail --location --retry 3 "$iso_url" -o "$temporary"
  printf '%s  %s\n' "$iso_sha" "$temporary" | sha256sum -c -
  mv -f -- "$temporary" scos-live.iso
fi
printf 'Base SCOS ISO ready. No target VM/IP, cluster credentials or OCI image import were needed.\n'
printf 'Next: build cluster-specific media with step 03, then upload/import with step 04. No target IP is needed yet.\n'
REMOTE
