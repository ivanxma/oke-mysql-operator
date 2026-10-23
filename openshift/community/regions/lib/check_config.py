#!/usr/bin/env python3
"""Offline checks only. Never import OCI or contact an administration host."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
REGIONS = ('uk-london-1', 'eu-frankfurt-1')
KEYS = ('ADMIN_HOST', 'ADMIN_WORKDIR', 'CLUSTER_NAME', 'BASE_DOMAIN', 'MACHINE_CIDR',
        'POD_CIDR', 'SERVICE_CIDR', 'OCI_REGION', 'OCI_COMPARTMENT_ID', 'OCI_COMPARTMENT_NAME',
        'OKD_STATE_DIR', 'KUBECONFIG')


def peer_config(path):
    # Config is a trusted, private Bash file, just like the installation config.
    code = 'import os,json; print(json.dumps({k:os.environ.get(k, "") for k in ' + repr(KEYS) + '}))'
    result = subprocess.run(['bash', '-c', 'set -e; set -a; if [[ -f ${1%.env}.optional.env ]]; then source "${1%.env}.optional.env"; fi; source "$1"; python3 -c "$2"',
                             '--', str(path), code], env={'PATH': os.environ['PATH'], 'HOME': os.environ['HOME']},
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def validate(cfg, peers=(), config_path=None, preparation=False):
    errors, warnings = [], []
    required = ('ADMIN_HOST', 'ADMIN_USER', 'ADMIN_WORKDIR', 'TARGET_USER', 'NODE_IP', 'SSH_IDENTITY',
                'CLUSTER_NAME', 'BASE_DOMAIN', 'MACHINE_CIDR', 'POD_CIDR', 'SERVICE_CIDR',
                'OCI_REGION', 'OCI_COMPARTMENT_ID', 'OCI_COMPARTMENT_NAME')
    if preparation:
        required=tuple(key for key in required if key!='NODE_IP')
    for key in required:
        if not cfg.get(key) or 'REPLACE' in cfg.get(key, ''):
            errors.append('Run 05-register-node-ip.sh with your explicitly selected address before this installation stage.' if key=='NODE_IP' else 'Set ' + key + ' in the private regional configuration.')
    if cfg.get('OCI_REGION') != cfg.get('OKD_REGION') or cfg.get('OKD_REGION') not in REGIONS:
        errors.append('Selected folder and OCI_REGION must match a supported region.')
    if cfg.get('OCI_AUTH') != 'instance_principal':
        errors.append('OCI_AUTH must be instance_principal.')
    if cfg.get('ADMIN_WORKDIR'):
        work = Path(cfg['ADMIN_WORKDIR'])
        if not work.is_absolute() or len(work.parts) < 4 or work.parts[1] != 'home' or '..' in work.parts:
            errors.append('ADMIN_WORKDIR must be a dedicated workspace beneath a home directory.')
        kube = Path(cfg.get('KUBECONFIG', ''))
        if not kube.is_absolute() or '..' in kube.parts or work not in kube.parents or ':' in str(kube):
            errors.append('KUBECONFIG must be one absolute file inside this regional ADMIN_WORKDIR.')
    state = Path(cfg.get('OKD_STATE_DIR', ''))
    private = (ROOT / 'internal_doc').resolve()
    if not state.is_absolute() or private not in state.resolve().parents:
        errors.append('OKD_STATE_DIR must be inside the ignored internal_doc directory.')
    if config_path:
        path = Path(config_path)
        if private not in path.resolve().parents:
            errors.append('Keep regional configuration inside internal_doc.')
        if path.stat().st_mode & 0o077:
            errors.append('Private regional config must have mode 600 or stricter.')
    networks = {}
    for key in ('MACHINE_CIDR', 'POD_CIDR', 'SERVICE_CIDR'):
        try:
            networks[key] = ipaddress.IPv4Network(cfg.get(key, ''))
        except ValueError:
            errors.append(key + ' must be a valid aligned IPv4 CIDR.')
    for i, (key, net) in enumerate(networks.items()):
        for other, candidate in list(networks.items())[i + 1:]:
            if net.overlaps(candidate):
                errors.append(key + ' overlaps ' + other + '.')
    try:
        admin = ipaddress.IPv4Address(cfg.get('ADMIN_HOST', ''))
    except ValueError:
        errors.append('ADMIN_HOST must be an IPv4 address.')
        admin=None
    if preparation and not cfg.get('NODE_IP'):
        pass  # No target is selected during preparation; this is not a warning.
    else:
        try:
            node = ipaddress.IPv4Address(cfg.get('NODE_IP', ''))
            if node == admin:
                errors.append('The target VM must be separate from the administration host.')
            if 'MACHINE_CIDR' in networks:
                net = networks['MACHINE_CIDR']
                if node not in net or node in (net.network_address, net.network_address + 1, net.broadcast_address):
                    errors.append('NODE_IP must be a usable OCI address in MACHINE_CIDR.')
        except ValueError:
            errors.append('NODE_IP must be an IPv4 address registered through 05-register-node-ip.sh.')
    for key in ('ADMIN_USER', 'TARGET_USER'):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*', cfg.get(key, '')):
            errors.append(key + ' must be a Linux account name.')
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if not re.fullmatch(label, cfg.get('CLUSTER_NAME', '')):
        errors.append('CLUSTER_NAME must be a lowercase DNS label.')
    domain = cfg.get('BASE_DOMAIN', '')
    if not domain or not all(re.fullmatch(label, part) for part in domain.split('.')) or domain.endswith('example.com'):
        errors.append('BASE_DOMAIN must be a controlled DNS domain.')
    key = Path(cfg.get('SSH_IDENTITY', ''))
    if not key.is_file():
        errors.append('SSH_IDENTITY must identify an existing local private key.')
    elif key.stat().st_mode & 0o077:
        errors.append('The local SSH key must have mode 600 or stricter.')
    if not cfg.get('OCI_COMPARTMENT_ID', '').startswith('ocid1.compartment.'):
        errors.append('OCI_COMPARTMENT_ID must identify the authorized compartment.')
    if not cfg.get('OCI_BUCKET') or not cfg.get('OCI_NAMESPACE'):
        warnings.append('Run 02-create-bucket.sh after step 01 to provision/register regional bucket settings.')
    if not cfg.get('CLIENT_CIDR'):
        warnings.append('Set CLIENT_CIDR before validating browser/API security rules remotely.')
    for peer in peers:
        if peer.get('OCI_COMPARTMENT_ID') and peer['OCI_COMPARTMENT_ID'] != cfg.get('OCI_COMPARTMENT_ID'):
            errors.append('Both sites must select the same authorized compartment.')
        if peer.get('OCI_COMPARTMENT_NAME') and peer['OCI_COMPARTMENT_NAME'] != cfg.get('OCI_COMPARTMENT_NAME'):
            errors.append('Both sites must select the same authorized compartment name.')
        if peer.get('CLUSTER_NAME') and (cfg.get('CLUSTER_NAME'), cfg.get('BASE_DOMAIN')) == (peer.get('CLUSTER_NAME'), peer.get('BASE_DOMAIN')):
            errors.append('The other regional cluster already uses this cluster DNS identity.')
        peer_state = peer.get('OKD_STATE_DIR') or str(ROOT/'internal_doc/regions'/peer.get('OCI_REGION', '')/'.state')
        if state.resolve() == Path(peer_state).resolve():
            errors.append('Local SSH/installer state must be separate between regions.')
        if peer.get('ADMIN_HOST') == cfg.get('ADMIN_HOST') and peer.get('ADMIN_WORKDIR'):
            a, b = Path(cfg['ADMIN_WORKDIR']), Path(peer['ADMIN_WORKDIR'])
            if a == b or a in b.parents or b in a.parents:
                errors.append('Sites sharing an admin host need separate, non-nested ADMIN_WORKDIR paths.')
        for key, net in networks.items():
            for other in ('MACHINE_CIDR', 'POD_CIDR', 'SERVICE_CIDR'):
                try:
                    if net.overlaps(ipaddress.IPv4Network(peer.get(other, ''))):
                        errors.append(key + ' overlaps the other region\x27s ' + other + '; allocate distinct cross-site ranges.')
                except ValueError:
                    warnings.append('The other region\x27s ' + other + ' is incomplete; cross-site overlap is unverified.')
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def main():
    parser=argparse.ArgumentParser(description='Offline regional configuration checks')
    parser.add_argument('--preparation',action='store_true',help='Allow an unset target IP for admin/tool/bucket/base-image preparation')
    options=parser.parse_args()
    region = os.environ.get('OKD_REGION', '')
    peers = []
    for other in REGIONS:
        if other != region:
            path = ROOT/'internal_doc/regions'/other/'community.env'
            if path.is_file():
                peers.append(peer_config(path))
    errors, warnings = validate(os.environ, peers, os.environ['OKD_CONFIG_FILE'],preparation=options.preparation)
    for text in errors:
        print('FAIL: ' + text)
    for text in warnings:
        print('WARN: ' + text)
    if not peers:
        print('WARN: No other regional profile found; cross-site isolation is unverified.')
    if errors:
        return 1
    print('PASS: Local regional configuration checks. No SSH/OCI calls were made.')
    if options.preparation:
        print('NEXT: Prepare host, bucket, OKD tools and base SCOS image; a target VM/IP is not required yet.')
        return 0
    print('NEXT: Remote preflight must verify DNS, routes, NSG/security-list rules, capacity and OCI permissions.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit('ERROR: Regional configuration could not be checked: ' + type(error).__name__)
