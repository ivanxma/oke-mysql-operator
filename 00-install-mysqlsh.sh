#!/usr/bin/env bash
# Install/update the administration host's MySQL Shell from the Innovation tools repo.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

[[ $(uname -s) == Linux ]] || die 'Run this installer on Enterprise Linux 9.'
# shellcheck disable=SC1091
source /etc/os-release
[[ ${VERSION_ID%%.*} == 9 ]] || die "Expected Linux 9; found ${PRETTY_NAME:-unknown}."
case "$ID" in ol|rhel|rocky|almalinux|centos) ;; *) die "Unsupported Linux distribution: $ID" ;; esac
case "$(uname -m)" in x86_64|aarch64) ;; *) die 'Supported architectures: x86_64 and aarch64.' ;; esac
need dnf
need rpm

log 'Installing DNF repository tools and CA certificates.'
as_root dnf --disablerepo='mysql*' install -y ca-certificates dnf-plugins-core

# Official EL9 repository setup RPM; the Shell package itself is not version-pinned.
# This package also replaces the older mysql80/mysql84 repository setup RPMs.
repo_package=mysql97-community-release
repo_url=https://repo.mysql.com/mysql97-community-release-el9-1.noarch.rpm
if rpm -q "$repo_package" >/dev/null 2>&1; then
    log 'Official MySQL Yum repository setup package is already installed.'
else
    log 'Installing the official MySQL Yum repository setup RPM using DNF.'
    as_root dnf --disablerepo='mysql*' install -y "$repo_url"
fi

# Shell is in the tools repository. Select Innovation instead of the default LTS.
# Keep this choice for future Shell updates without changing server repo selection.
log 'Selecting the MySQL Tools Innovation channel.'
as_root dnf config-manager --set-disabled 'mysql-tools*'
as_root dnf config-manager --set-enabled mysql-tools-innovation-community

# Refresh metadata and request the newest available mysql-shell package. DNF also
# upgrades an older installed package. OS repos remain available for dependencies.
log 'Installing/updating MySQL Shell to the latest available Innovation release.'
as_root dnf --refresh --best --disablerepo='mysql*' \
    --enablerepo=mysql-tools-innovation-community --setopt=gpgcheck=1 \
    install -y mysql-shell

need mysqlsh
mysqlsh --version
rpm -q mysql-shell
log 'MySQL Shell ready. Connect through step 08 with: mysqlsh --sql --host 127.0.0.1 --port 3306 --user root --password'
