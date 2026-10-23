"""Verify a console-supplied fingerprint before replacing one private SSH entry."""
import ipaddress
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def command(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=45, **kwargs)


def refresh(state, address, fingerprint, action):
    ipaddress.IPv4Address(address)
    if not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint):
        raise ValueError('Supply the SHA256 ED25519 fingerprint from the target OCI serial console')
    if action not in ('preview', 'apply'):
        raise ValueError('Expected preview/apply')
    state = Path(state)
    config, known = state/'ssh_config', state/'known_hosts'
    if state.is_symlink() or any(p.is_symlink() for p in (config, known)):
        raise ValueError('Refusing symlink SSH state')
    if not config.is_file() or not known.is_file():
        raise ValueError('Missing SSH state; run step 01 --ssh-only and verify the admin host first')
    if state.stat().st_mode & 0o077 or known.stat().st_mode & 0o077:
        raise ValueError('Private SSH state directory/files must restrict group/other access')
    settings = command(['ssh','-F',str(config),'-G','okd-target'], check=True).stdout
    fields = dict(line.split(None, 1) for line in settings.splitlines() if ' ' in line)
    if (fields.get('hostname') != address or fields.get('port') != '22' or
            fields.get('proxyjump') != 'okd-admin' or fields.get('hostkeyalias') or
            shlex.split(fields.get('userknownhostsfile', '')) != [str(known)]):
        raise ValueError('SSH state differs from selected target or expected step-01 layout; rerun step 01 --ssh-only')
    original = known.read_bytes()
    print('Checking target identity through the verified admin SSH connection...', flush=True)
    scan = command(['ssh','-F',str(config),'-o','StrictHostKeyChecking=yes','okd-admin',
                    shlex.join(['ssh-keyscan','-T','10','-t','ed25519',address])])
    if scan.returncode:
        raise ValueError('Cannot scan target through admin; check admin host identity, authentication and target TCP 22. No saved keys changed')
    lines = set()
    for line in scan.stdout.splitlines():
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3 or parts[0] != address or parts[1] != 'ssh-ed25519':
            raise ValueError('Unexpected target key-scan response; no saved keys changed')
        lines.add(line)
    if len(lines) != 1:
        raise ValueError('Expected exactly one ED25519 target key; no saved keys changed')
    with tempfile.TemporaryDirectory(prefix='.host-key-check-', dir=state) as temp:
        candidate = Path(temp)/'candidate'
        candidate.write_text(next(iter(lines))+'\n')
        candidate.chmod(0o600)
        inspected = command(['ssh-keygen','-lf',str(candidate)],check=True).stdout.split()
        if len(inspected) < 2 or inspected[1] != fingerprint:
            raise ValueError('Target fingerprint DOES NOT MATCH console fingerprint; no saved keys changed')
        print('PASS: Target ED25519 fingerprint matches independently supplied value.', flush=True)
        # Pin exactly that key for a read-only authenticated connection before saving.
        candidate_option = 'UserKnownHostsFile="'+str(candidate).replace('\\','\\\\').replace('"','\\"')+'"'
        connection = command(['ssh','-F',str(config),'-o','StrictHostKeyChecking=yes',
                              '-o',candidate_option,
                              '-o','GlobalKnownHostsFile=/dev/null','-o','UpdateHostKeys=no',
                              '-o','HostKeyAlgorithms=ssh-ed25519','okd-target','true'])
        if connection.returncode:
            raise ValueError('Pinned target SSH authentication failed; verify target account/key and admin connection. No saved keys changed')
        print('PASS: Target SSH authentication succeeded with the verified key.', flush=True)
        if action == 'preview':
            print('PREVIEW COMPLETE: no saved identities changed. Repeat with --apply to save this target key.')
            return
        # Edit a copy; preserve unrelated identities and abort on concurrent updates.
        replacement = Path(temp)/'known_hosts'
        replacement.write_bytes(original)
        replacement.chmod(0o600)
        command(['ssh-keygen','-R',address,'-f',str(replacement)],check=True)
        with replacement.open('a') as output:
            output.write('\n'+candidate.read_text())
        if known.is_symlink() or known.read_bytes() != original:
            raise ValueError('Saved host keys changed concurrently; retry after review. No keys replaced')
        handle, backup = tempfile.mkstemp(prefix='known_hosts.backup-',dir=state)
        with os.fdopen(handle,'wb') as output:
            output.write(original)
        os.replace(replacement,known)
        print('APPLIED: replaced only the registered target host key. Backup: '+backup)
        print('NEXT: ./05-verify-dns.sh --wait 600; then ./00-validate-environment.sh --existing-target')


if __name__ == '__main__':
    try:
        refresh(*sys.argv[1:])
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        if isinstance(error, subprocess.SubprocessError):
            print('FAIL: SSH/key verification command failed or timed out; inspect access and saved SSH state.', file=sys.stderr)
        else:
            print('FAIL: '+str(error),file=sys.stderr)
        sys.exit(1)
