#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then echo 'Usage: ./02-download-okd.sh (uses private deployment configuration and saved SSH host)'; exit 0; fi
load_config preparation; load_host
[[ $OKD_VERSION =~ ^[A-Za-z0-9._-]+$ ]] || fail 'Invalid OKD_VERSION'
remote_run "$OKD_VERSION" "$ADMIN_WORKDIR" <<'REMOTE'
set -euo pipefail
version=$1
work=$2
mkdir -p "$work/downloads/$version"
cd "$work/downloads/$version"
base="https://github.com/okd-project/okd/releases/download/$version"
client="openshift-client-linux-amd64-rhel9-$version.tar.gz"
installer="openshift-install-linux-$version.tar.gz"
for f in sha256sum.txt sha256sum.txt.asc release.txt "$client" "$installer"; do
  curl --fail --location --retry 3 "$base/$f" -o "$f"
done
# Authenticate the published checksums with the project's signing key.
curl --fail --location --retry 3 https://www.okd.io/okd-maintainers.pub -o okd-maintainers.pub
export GNUPGHOME="$work/gnupg"
mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
gpg --batch --import okd-maintainers.pub
expected=82E40BC298E7F5D2877586FDF23087F7B3AF7E5A
gpg --batch --with-colons --fingerprint "$expected" | awk -F: '$1=="fpr" {print $10}' | grep -Fx "$expected" >/dev/null
gpg --batch --verify sha256sum.txt.asc sha256sum.txt
for f in "$client" "$installer" release.txt; do
  awk -v f="$f" '$2==f || $2=="*" f {print}' sha256sum.txt > "$f.sha256"
  [[ $(wc -l < "$f.sha256") -eq 1 ]] || { echo "Missing/duplicate checksum: $f" >&2; exit 1; }
  sha256sum -c "$f.sha256"
done
mkdir -p client installer
tar -xzf "$client" -C client oc kubectl
tar -xzf "$installer" -C installer openshift-install
install -m 0755 client/oc client/kubectl installer/openshift-install "$work/bin/"
"$work/bin/oc" version --client
"$work/bin/openshift-install" version
"$work/bin/openshift-install" version | grep -F "$version" >/dev/null
printf '%s\n' "$version" > "$work/downloaded-version"
printf '\nOKD tools installed privately under %s/bin\n' "$work"
REMOTE
