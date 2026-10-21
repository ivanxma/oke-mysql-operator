#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then echo 'Usage: ./03-build-control-plane-image.sh (set DNS, node IP and disk in the private configuration)'; exit 0; fi
load_config; load_host; validate_cluster
[[ $NODE_IP != "$SSH_HOST" ]] || fail 'NODE_IP must identify a separate new CoreOS VM; do not target the OL9 preparation host.'
remote_run "$OKD_VERSION" "$CLUSTER_NAME" "$BASE_DOMAIN" "$NODE_IP" "$MACHINE_CIDR" "$POD_CIDR" "$SERVICE_CIDR" "$INSTALL_DISK" "$ADMIN_WORKDIR" <<'REMOTE'
set -euo pipefail
version=$1 cluster=$2 domain=$3 node_ip=$4 machine=$5 pod=$6 service=$7 disk=$8
work=$9
export PATH="$work/bin:$PATH"
[[ -f "$work/downloaded-version" && $(cat "$work/downloaded-version") == "$version" ]] || { echo 'Run step 02 for this version first.' >&2; exit 1; }
[[ ! -e "$work/sno" ]] || { echo "Existing $work/sno retained. Use a new preparation workspace for a new installation." >&2; exit 1; }
# Check prerequisite DNS before generating credentials tied to this cluster name.
for name in "api.$cluster.$domain" "api-int.$cluster.$domain" "console-openshift-console.apps.$cluster.$domain"; do
  dig +short A "$name" | grep -Fx "$node_ip" >/dev/null || { echo "DNS $name must resolve to $node_ip" >&2; exit 1; }
done
umask 077
cd "$work"
openshift-install coreos print-stream-json > coreos-stream.json
iso_url=$(jq -er '.architectures.x86_64.artifacts.metal.formats.iso.disk.location' coreos-stream.json)
iso_sha=$(jq -er '.architectures.x86_64.artifacts.metal.formats.iso.disk.sha256' coreos-stream.json)
[[ $iso_url == https://* ]] || { echo 'Expected HTTPS ISO URL' >&2; exit 1; }
if [[ ! -f scos-live.iso ]] || ! printf '%s  %s\n' "$iso_sha" scos-live.iso | sha256sum -c - >/dev/null 2>&1; then
  curl --fail --location --retry 3 "$iso_url" -o scos-live.iso
fi
printf '%s  %s\n' "$iso_sha" scos-live.iso | sha256sum -c -
mkdir sno
python3 - "$work" "$cluster" "$domain" "$machine" "$pod" "$service" "$disk" <<'PY'
import json,sys
from pathlib import Path
work,cluster,domain,machine,pod,service,disk=sys.argv[1:]
p=Path(work)
config={"apiVersion":"v1","baseDomain":domain,"metadata":{"name":cluster},
 "compute":[{"name":"worker","architecture":"amd64","replicas":0}],
 "controlPlane":{"name":"master","architecture":"amd64","replicas":1},
 "networking":{"networkType":"OVNKubernetes","machineNetwork":[{"cidr":machine}],
 "clusterNetwork":[{"cidr":pod,"hostPrefix":23}],"serviceNetwork":[service]},
 "platform":{"none":{}},"bootstrapInPlace":{"installationDisk":disk},
 "pullSecret":(p/'pull-secret.json').read_text().strip(),
 "sshKey":(p/'ssh_key.pub').read_text().strip()}
json.loads(config['pullSecret'])
# JSON is a YAML subset; avoid installing a YAML library on the admin VM.
text=json.dumps(config,indent=2)+'\n'
(p/'sno/install-config.yaml').write_text(text)
(p/'install-config.backup.yaml').write_text(text)
PY
openshift-install --dir sno create single-node-ignition-config
podman pull quay.io/coreos/coreos-installer:release
ci_image=$(podman image inspect quay.io/coreos/coreos-installer:release --format '{{.Digest}}')
printf '%s\n' "$ci_image" > coreos-installer-digest.txt
ci() { podman run --rm --security-opt label=disable -v "$work:/data" -w /data "quay.io/coreos/coreos-installer@$ci_image" "$@"; }
ci iso ignition embed --ignition-file /data/sno/bootstrap-in-place-for-live-iso.ign \
  --output /data/okd-sno-oci.iso /data/scos-live.iso
# OCI boots this image from the boot volume itself. Run live media from RAM
# before bootstrap-in-place rewrites that volume. This OCI adaptation needs a lab validation.
ci iso kargs modify --append console=ttyS0,115200n8 --append rd.live.ram=1 \
  --append rd.live.overlay=none --append coreos.liveiso.fromram /data/okd-sno-oci.iso
ci iso kargs show /data/okd-sno-oci.iso
sha256sum okd-sno-oci.iso > okd-sno-oci.iso.sha256
python3 - "$version" "$cluster.$domain" "$disk" <<'PY'
import json,sys
from pathlib import Path
version,domain,disk=sys.argv[1:]
Path('control-plane-build.json').write_text(json.dumps(dict(version=version,
    cluster_domain=domain,architecture='x86_64',role='control-plane',install_disk=disk),indent=2)+'\n')
PY
printf '%s\n' "$node_ip" > expected-node-ip
printf '%s\n' "$cluster.$domain" > cluster-domain
printf '\nImage prepared: %s/okd-sno-oci.iso\n' "$work"
printf 'Intended CoreOS launch: IP %s, installation disk %s (rebuild/release the existing OL9 target IP explicitly).\n' "$node_ip" "$disk"
printf 'No disks were reformatted. Import and launch instructions are in internal_doc/openshift/community-sno.html.\n'
REMOTE
