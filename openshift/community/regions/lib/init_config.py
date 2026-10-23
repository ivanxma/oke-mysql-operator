#!/usr/bin/env python3
"""Create private regional templates locally without replacing existing files."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[4]
REGIONS = ('uk-london-1', 'eu-frankfurt-1')


def main():
    region = sys.argv[1]
    if region not in REGIONS or len(sys.argv) != 2:
        raise ValueError('Usage: 00-init-config.sh (no arguments)')
    folder = ROOT / 'generated/openshift' / region
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    template = (ROOT / 'openshift/community/community.env.example').read_text()
    template = template.replace('# Copy this template to internal_doc/openshift/community.env (ignored by Git).',
                                '# Private regional deployment settings; never publish this file.')
    replacements = {'OCI_REGION=': 'OCI_REGION=' + region,
                    'TARGET_USER=opc': 'TARGET_USER=core',
                    'CLUSTER_NAME=okd-sno': 'CLUSTER_NAME=okd-london' if region == 'uk-london-1' else 'CLUSTER_NAME=okd-frankfurt'}
    if region == 'uk-london-1':
        replacements.update({'POD_CIDR=10.128.0.0/14': 'POD_CIDR=10.132.0.0/14',
                             'SERVICE_CIDR=172.30.0.0/16': 'SERVICE_CIDR=172.31.0.0/16'})
    lines = [replacements.get(line, line) for line in template.splitlines()]
    content = '\n'.join(lines) + '\n'
    optional = folder / 'community.optional.env'
    try:
        fd_optional = os.open(optional, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd_optional, 'w') as stream:
            stream.write((ROOT / 'openshift/community/community.optional.env.example').read_text())
    target = folder / 'community.env'
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print('Existing private regional configuration retained.')
        return
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    print('Created private regional template. Fill required settings before checking or running steps.')
    print('No SSH, OCI or cluster commands were executed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit('ERROR: ' + str(error))
