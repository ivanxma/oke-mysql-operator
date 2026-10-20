#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
validate_config
for command in curl tar sha256sum install; do need "$command"; done
[[ $(uname -s) == Linux ]] || die 'This installer requires Linux.'
case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) die 'Supported architectures: x86_64 and aarch64.' ;; esac
if command -v helm >/dev/null 2>&1; then
    installed="$(helm version --template '{{.Version}}')"
    [[ "$installed" == "$HELM_VERSION" ]] || die "Helm $installed is already on PATH; requested $HELM_VERSION. Set HELM_VERSION to use it, or manage the existing binary first."
    log "Helm $installed already installed."
    exit 0
fi
work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
archive="helm-${HELM_VERSION}-linux-${arch}.tar.gz"
curl --fail --show-error --silent --location --retry 3 "https://get.helm.sh/$archive" -o "$work_dir/$archive"
# Pinned published SHA256 values for the default version. Overrides use upstream checksum.
case "$HELM_VERSION/$arch" in
    v4.3.1/amd64) checksum=0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b ;;
    v4.3.1/arm64) checksum=8cf8f66ecbc252ac8f7f06cca68aba88508ddfde3a6a34feb1678ad1694b4f88 ;;
    *) checksum="$(curl -fsSL --retry 3 "https://get.helm.sh/$archive.sha256")" ;;
esac
[[ "$checksum" =~ ^[a-fA-F0-9]{64}$ ]] || die 'Invalid Helm SHA256 checksum.'
(cd "$work_dir" && printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check -)
tar -xzf "$work_dir/$archive" -C "$work_dir" "linux-$arch/helm"
as_root install -m 0755 "$work_dir/linux-$arch/helm" /usr/local/bin/helm
need helm
helm version --short
