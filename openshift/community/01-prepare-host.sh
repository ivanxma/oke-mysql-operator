#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  echo 'Usage: ./01-prepare-host.sh [--ssh-only | --inspect-target | <target-account> <target-private-IP> <local-SSH-key>]'
  echo 'Default: prepare the OL9 admin host; no existing target VM is needed.'; exit 0
fi
inspect_target=false
ssh_only=false
if [[ ${1:-} == --ssh-only ]]; then ssh_only=true; shift; fi
if [[ ${1:-} == --inspect-target ]]; then inspect_target=true; shift; fi
[[ $# -eq 0 || $# -eq 3 ]] || fail 'Supply either no arguments (private config) or target account, private IP and local SSH key.'
if [[ $# -eq 3 ]]; then inspect_target=true; fi
load_config preparation
TARGET_USER=${1:-${TARGET_USER:-opc}}
TARGET_HOST=${2:-${NODE_IP:-}}
SSH_IDENTITY=${3:-${SSH_IDENTITY:-}}
if [[ -n $TARGET_HOST ]]; then validate_host "$TARGET_USER" "$TARGET_HOST"; fi
if $inspect_target; then [[ -n $TARGET_HOST ]] || fail 'Set NODE_IP before inspecting an existing target.'; fi
validate_host "$ADMIN_USER" "$ADMIN_HOST"
[[ $TARGET_HOST != "$ADMIN_HOST" ]] || fail 'Admin and target must be different VMs.'
[[ -f $SSH_IDENTITY ]] || fail 'SSH private key not found on this local machine.'
SSH_IDENTITY=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$SSH_IDENTITY")
[[ $SSH_IDENTITY != *'"'* && $SSH_IDENTITY != *$'\n'* ]] || fail 'Unsupported character in SSH key path.'
[[ $ADMIN_WORKDIR == /home/* && $ADMIN_WORKDIR != *$'\n'* ]] || fail 'ADMIN_WORKDIR must be under /home/.'
umask 077
mkdir -p "$STATE_DIR"
# The private key stays on the invoking host. ProxyJump authenticates both hops locally.
cat > "$STATE_DIR/ssh_config" <<CONFIG
Host okd-admin
  HostName $ADMIN_HOST
  User $ADMIN_USER
  IdentityFile "$SSH_IDENTITY"
  IdentitiesOnly yes
  ForwardAgent no
  ConnectTimeout 15
  BatchMode yes
  StrictHostKeyChecking accept-new
  UserKnownHostsFile "$STATE_DIR/known_hosts"
CONFIG
if [[ -n $TARGET_HOST ]]; then
cat >> "$STATE_DIR/ssh_config" <<CONFIG
Host okd-target
  HostName $TARGET_HOST
  User $TARGET_USER
  IdentityFile "$SSH_IDENTITY"
  IdentitiesOnly yes
  ForwardAgent no
  ProxyJump okd-admin
  ConnectTimeout 15
  BatchMode yes
  StrictHostKeyChecking accept-new
  UserKnownHostsFile "$STATE_DIR/known_hosts"
CONFIG
fi
need ssh-keygen
ssh-keygen -y -f "$SSH_IDENTITY" > "$STATE_DIR/ssh_key.pub"
public_key=$(cat "$STATE_DIR/ssh_key.pub")
SSH_USER=$ADMIN_USER SSH_HOST=$ADMIN_HOST
printf 'SSH_USER=%q\nSSH_HOST=%q\nSSH_IDENTITY=%q\nTARGET_USER=%q\nTARGET_HOST=%q\n' \
 "$SSH_USER" "$SSH_HOST" "$SSH_IDENTITY" "$TARGET_USER" "$TARGET_HOST" > "$STATE_DIR/host.env"
if $ssh_only; then
  printf 'SSH state refreshed locally; no target connection or admin package changes.\n'
  exit 0
fi
if $inspect_target; then
printf 'Inspecting target %s@%s through %s@%s.\n' "$TARGET_USER" "$TARGET_HOST" "$ADMIN_USER" "$ADMIN_HOST"
target_run <<'REMOTE'
set -euo pipefail
source /etc/os-release
printf '\nTarget inventory (no changes):\n'
printf 'OS: %s\n' "$PRETTY_NAME"
ip -4 -o addr show scope global
nproc
free -h
lsblk -d -o NAME,SIZE,TYPE,MODEL
[[ -d /sys/firmware/efi ]] && echo 'Boot mode: UEFI'
REMOTE
fi
printf '\nPreparing admin workspace on %s@%s:%s\n' "$ADMIN_USER" "$ADMIN_HOST" "$ADMIN_WORKDIR"
remote_run "$ADMIN_WORKDIR" "$public_key" "$KUBECONFIG" "$OCI_REGION" "${OCI_COMPARTMENT_ID:-}" "${OKD_REGION:-}" <<'REMOTE'
set -euo pipefail
work=$1 public_key=$2 kubeconfig=$3 region=$4 compartment=$5 selected_region=$6
source /etc/os-release
[[ $ID == ol && ${VERSION_ID%%.*} == 9 ]] || { echo 'Expected Oracle Linux 9 admin host.' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'Expected x86_64 admin host.' >&2; exit 1; }
umask 077
mkdir -p "$work/downloads" "$work/bin"
free_kb=$(df -Pk "$work" | awk 'NR==2 {print $4}')
(( free_kb >= 10*1024*1024 )) || { echo 'Need at least 10 GiB free for tools, container images and ISO preparation; 30 GiB recommended.' >&2; exit 1; }
sudo -n true || { echo 'Passwordless sudo is required for package preparation.' >&2; exit 1; }
sudo -n dnf install -y ca-certificates curl wget tar gzip jq python3 openssh-clients \
  bind-utils nmap-ncat podman chrony openssl gnupg2 python3-pip
if ! python3 -c 'import oci' >/dev/null 2>&1; then
  python3 -m venv "$work/oci-sdk"
  "$work/oci-sdk/bin/python" -m pip install 'oci==2.187.0'
fi
sudo -n systemctl enable --now chronyd
printf '%s\n' "$public_key" > "$work/ssh_key.pub"
if [[ ! -f "$work/pull-secret.json" ]]; then
  printf '%s\n' '{"auths":{"fake":{"auth":"aWQ6cGFzcwo="}}}' > "$work/pull-secret.json"
fi
chmod 600 "$work/pull-secret.json"
# Install the shared interactive environment on the admin host. Existing shell
# A regional workspace selects its own kubeconfig; legacy selection is preserved.
{
  printf 'export ADMIN_WORKDIR=%q\n' "$work"
  printf 'export OCI_REGION=%q OCI_COMPARTMENT_ID=%q\n' "$region" "$compartment"
  printf 'unset OCI_BUCKET OCI_NAMESPACE\n'
  printf 'if [[ -f "$ADMIN_WORKDIR/oci-bucket.env" ]]; then source "$ADMIN_WORKDIR/oci-bucket.env" || { return 1 2>/dev/null || exit 1; }; fi\n'
  if [[ -n $selected_region ]]; then
    printf 'export KUBECONFIG=%q\n' "$kubeconfig"
  else
    printf 'if [[ -z ${KUBECONFIG:-} ]]; then export KUBECONFIG=%q; fi\n' "$kubeconfig"
  fi
  printf 'export PATH="$ADMIN_WORKDIR/bin:$PATH"\n'
} > "$work/env.sh"
chmod 600 "$work/env.sh"
chronyc tracking
printf '\nAdmin workspace ready: %s\n' "$work"
printf 'No target packages, partitions or boot volumes were changed.\n'
REMOTE
printf 'SSH_USER=%q\nSSH_HOST=%q\nSSH_IDENTITY=%q\nTARGET_USER=%q\nTARGET_HOST=%q\n' \
 "$SSH_USER" "$SSH_HOST" "$SSH_IDENTITY" "$TARGET_USER" "$TARGET_HOST" > "$STATE_DIR/host.env"
printf '\nNext: set BASE_DOMAIN in the private deployment config, then run ./02-create-bucket.sh and ./02-download-okd.sh\n'
