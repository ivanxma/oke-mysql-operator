#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./09-build-worker-image.sh (private configuration; downloads/builds on admin host)'
  exit 0
fi
load_config; load_host; validate_cluster
remote_run "$ADMIN_WORKDIR" "$KUBECONFIG" "$OKD_VERSION" "$CLUSTER_NAME.$BASE_DOMAIN" "${WORKER_INSTALL_DISK:-/dev/sda}" <<'REMOTE'
set -euo pipefail
work=$1 selected_kubeconfig=$2 version=$3 domain=$4 disk=$5
source "$work/env.sh"
export KUBECONFIG=$selected_kubeconfig
export PATH="$work/bin:$PATH"
umask 077
[[ -r $KUBECONFIG && $disk == /dev/* && $disk != *[[:space:]]* ]] || { echo 'Invalid kubeconfig or worker disk.' >&2; exit 1; }
[[ $(oc config view --minify -o jsonpath='{.clusters[0].cluster.server}') == "https://api.$domain:6443" ]] || { echo 'Wrong cluster selected.' >&2; exit 1; }
[[ $(cat "$work/downloaded-version") == "$version" ]] || { echo 'Run step 02 for the selected release.' >&2; exit 1; }
oc get clusterversion version -o json | jq -e --arg v "$version" '
  .status.desired.version == $v and any(.status.conditions[]; .type=="Available" and .status=="True")
  and all(.status.conditions[]; .type!="Progressing" or .status=="False")' >/dev/null || {
  echo 'Cluster must have completed the selected release; do not build during an upgrade.' >&2; exit 1;
}
out="$work/worker-image"
[[ ! -e $out ]] || { echo 'Existing worker-image directory retained. Archive it explicitly before a fresh build.' >&2; exit 1; }
mkdir -m 700 "$out"
cd "$out"
openshift-install coreos print-stream-json > coreos-stream.json
url=$(jq -er '.architectures.x86_64.artifacts.metal.formats.iso.disk.location' coreos-stream.json)
sha=$(jq -er '.architectures.x86_64.artifacts.metal.formats.iso.disk.sha256' coreos-stream.json)
[[ $url == https://* && $sha =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid stream metadata.' >&2; exit 1; }
if [[ -f $work/scos-live.iso ]] && printf '%s  %s\n' "$sha" "$work/scos-live.iso" | sha256sum -c - >/dev/null 2>&1; then
  cp --reflink=auto "$work/scos-live.iso" scos-live.iso
else
  curl --fail --location --proto '=https' --proto-redir '=https' --retry 3 "$url" -o scos-live.iso
fi
printf '%s  scos-live.iso\n' "$sha" | sha256sum -c -
oc -n openshift-machine-api get secret worker-user-data -o json > worker-secret.json
python3 - "$domain" <<'PY'
import base64, json, sys
from pathlib import Path
secret = json.loads(Path('worker-secret.json').read_text())
raw = base64.b64decode(secret['data']['userData'], validate=True)
config = json.loads(raw)
expected = 'https://api-int.' + sys.argv[1] + ':22623/config/worker'
sources = [x.get('source') for x in config['ignition']['config'].get('merge', [])]
if sources != [expected] or not config['ignition'].get('security', {}).get('tls', {}).get('certificateAuthorities'):
    raise SystemExit('Unexpected worker Ignition endpoint or missing CA; stop for review.')
Path('worker.ign').write_bytes(raw)
Path('worker-secret.json').unlink()
PY
ci_digest=$(cat "$work/coreos-installer-digest.txt")
[[ $ci_digest =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'Missing pinned CoreOS installer digest; run step 03 prerequisites.' >&2; exit 1; }
ci() { podman run --rm --security-opt label=disable -v "$out:/data" -w /data "quay.io/coreos/coreos-installer@$ci_digest" "$@"; }
# This is worker-only media. The booted installer rewrites the new VM boot disk.
# RAM boot releases that disk before installation; no fixed MAC/IP is embedded.
ci iso customize --dest-device "$disk" --dest-ignition /data/worker.ign \
  --dest-console ttyS0,115200n8 --live-karg-append console=ttyS0,115200n8 \
  --live-karg-append rd.live.ram=1 --live-karg-append rd.live.overlay=none \
  --live-karg-append coreos.liveiso.fromram --output /data/okd-worker-oci.iso /data/scos-live.iso
ci iso kargs show /data/okd-worker-oci.iso > live-kargs.txt
# Validate the embedded live installer configuration without exposing its contents.
ci iso ignition show /data/okd-worker-oci.iso > live-installer.ign
python3 - "$version" "$domain" "$disk" "$ci_digest" <<'PY'
import json, sys
from pathlib import Path
json.loads(Path('live-installer.ign').read_text())
version, domain, disk, digest = sys.argv[1:]
Path('build.json').write_text(json.dumps(dict(version=version, cluster_domain=domain,
    architecture='x86_64', role='worker', install_disk=disk, installer_digest=digest), indent=2)+'\n')
PY
sha256sum okd-worker-oci.iso > okd-worker-oci.iso.sha256
chmod 600 ./*
printf 'Worker media prepared in %s. Run step 10 to import the private OCI image.\n' "$out"
REMOTE
