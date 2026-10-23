"""Readable DNS checks over the configured SSH paths; never applies changes."""
import concurrent.futures
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


def interpret(name, expected, getent_output, dig_output):
    addresses = set()
    for line in getent_output.splitlines():
        fields = line.split()
        if fields:
            try:
                addresses.add(str(ipaddress.IPv4Address(fields[0])))
            except ValueError:
                pass
    code = re.search(r'status:\s*([A-Z]+)', dig_output)
    status = code.group(1) if code else 'unavailable'
    negative_ttl = None
    dns_addresses = set()
    for line in dig_output.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[2:4] == ['IN', 'SOA'] and fields[1].isdigit():
            negative_ttl = int(fields[1])
        if len(fields) == 5 and fields[2:4] == ['IN','A']:
            try:
                dns_addresses.add(str(ipaddress.IPv4Address(fields[4])))
            except ValueError:
                pass
    passed = addresses == {expected} and dns_addresses == {expected} and status == 'NOERROR'
    detail = 'OS: %s; DNS: %s%s' % (', '.join(sorted(addresses)) or 'unresolved', status,
                                    ' '+', '.join(sorted(dns_addresses)) if dns_addresses else '')
    if not passed and status == 'NXDOMAIN' and negative_ttl is not None:
        detail += '; negative-answer TTL %ss (cache/propagation may be pending)' % negative_ttl
    if passed:
        detail = expected
    return dict(name=name, passed=passed, detail=detail)


def query(name, expected):
    try:
        result = subprocess.run(['getent','ahostsv4',name], capture_output=True, text=True, timeout=10)
        addresses = result.stdout
    except (OSError, subprocess.TimeoutExpired):
        addresses = ''
    try:
        result = subprocess.run(['dig','+time=2','+tries=1',name,'A','+noall','+comments','+answer','+authority'],
                                capture_output=True, text=True, timeout=5)
        details = result.stdout
    except (OSError, subprocess.TimeoutExpired):
        details = ''
    return interpret(name, expected, addresses, details)


def probe(domain, expected, role):
    names = ['api.'+domain, 'api-int.'+domain, 'console-openshift-console.apps.'+domain]
    # Checking the console alone cannot detect an accidentally narrow apps record.
    names.append('probe-%s.apps.%s' % (time.time_ns(), domain))
    return dict(role=role, checks=[query(name, expected) for name in names])


def remote_probe(config, domain, expected, role):
    error = None
    try:
        command = shlex.join(['python3','-','probe',domain,expected,role])
        result = subprocess.run(['ssh','-F',config,'okd-'+('admin' if role=='ADMIN' else 'target'),command],
                                input=Path(__file__).read_text(), capture_output=True, text=True, timeout=65)
        if result.returncode:
            # Classify errors without printing remote output or private key paths.
            stderr = result.stderr.lower()
            reason = next((label for text, label in (
                ('permission denied', 'SSH authentication denied'),
                ('host key verification failed', 'SSH host identity verification failed'),
                ('remote host identification has changed', 'SSH host identity changed'),
                ('connection refused', 'SSH connection refused'),
                ('connection timed out', 'SSH connection timed out'),
                ('no route to host', 'No route to the SSH host'),
                ('connection reset', 'SSH connection reset'),
                ('connection closed', 'SSH connection closed'),
                ('python3: command not found', 'Remote Python 3 is missing'),
                ('python3: not found', 'Remote Python 3 is missing'),
            ) if text in stderr), 'SSH or remote Python probe failed')
            raise ValueError('%s (exit %s)' % (reason, result.returncode))
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get('role') != role:
            raise ValueError('Unexpected probe response')
        checks = report.get('checks')
        names = ['api.'+domain, 'api-int.'+domain, 'console-openshift-console.apps.'+domain]
        if (not isinstance(checks, list) or len(checks) != 4 or
                any(not isinstance(c, dict) or type(c.get('passed')) is not bool or
                    not isinstance(c.get('name'), str) or not isinstance(c.get('detail'), str)
                    for c in checks)):
            raise ValueError('Incomplete or malformed remote DNS response')
        if ([c['name'] for c in checks[:3]] != names or
                not re.fullmatch(r'probe-[0-9]+\.apps\.'+re.escape(domain), checks[3]['name'])):
            raise ValueError('Remote DNS response does not match selected cluster')
        return report
    except subprocess.TimeoutExpired:
        error = 'Remote probe exceeded 65s; SSH or DNS queries did not finish'
    except OSError:
        error = 'Cannot start SSH or read the probe script locally'
    except ValueError as failure:
        error = ('Remote probe returned invalid JSON' if isinstance(failure, json.JSONDecodeError)
                 else str(failure))
    return dict(role=role, checks=[], error=error)


def run(config, domain, expected, wait_seconds, interval):
    wait_seconds, interval = int(wait_seconds), int(interval)
    if wait_seconds < 0 or wait_seconds > 86400 or not 1 <= interval <= 300:
        raise ValueError('Use --wait 0..86400 and --interval 1..300 seconds')
    ipaddress.IPv4Address(expected)
    # Reject a stale target SSH stanza rather than querying another deployment.
    target = subprocess.run(['ssh','-F',config,'-G','okd-target'], capture_output=True, text=True, check=True)
    if not any(line.split() == ['hostname',expected] for line in target.stdout.splitlines()):
        raise ValueError('Saved target SSH address differs from NODE_IP; rerun step 01 --ssh-only')
    deadline = time.monotonic() + wait_seconds
    print('DNS verification — read-only; no configuration or resource changes.', flush=True)
    print('Expected address: '+expected, flush=True)
    while True:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            reports = list(executor.map(lambda role: remote_probe(config,domain,expected,role), ('ADMIN','TARGET')))
        passed = failed = unchecked = probe_errors = 0
        for report in reports:
            print('\n'+report['role'])
            if report.get('error'):
                unchecked += 4
                probe_errors += 1
                print('[ERROR] '+report['error'])
                print('[NOT CHECKED] All 4 DNS checks on this host; DNS status is unknown.')
                continue
            for check in report['checks']:
                ok = check['passed']
                passed += int(ok); failed += int(not ok)
                name = 'fresh apps wildcard' if check['name'].startswith('probe-') else check['name']
                print('[%s] %s → %s' % ('PASS' if ok else 'FAIL',name,check['detail']))
        print('\nDNS: %s PASS, %s FAIL, %s NOT CHECKED (8 expected); %s probe error(s).' %
              (passed,failed,unchecked,probe_errors), flush=True)
        if passed == 8 and not failed and not unchecked and not probe_errors:
            print('READY for full environment validation. DNS success alone does not prove cluster readiness.')
            return 0
        permanent = ('SSH authentication denied', 'SSH host identity', 'Remote Python 3 is missing')
        if any(any(reason in report.get('error', '') for reason in permanent) for report in reports):
            print('STOPPED: SSH identity/authentication or Python needs correction; DNS polling cannot resolve this.')
            print('For a confirmed target reinstall with a changed key: ./05-refresh-target-host-key.sh --apply, then rerun this check.')
            return 1
        remaining = deadline-time.monotonic()
        if wait_seconds == 0 or remaining <= 0:
            print('NOT READY. Resolve probe errors separately from DNS failures; no changes were made.')
            print('For NXDOMAIN, compare its remaining negative-answer TTL with --wait. Polling does not clear resolver caches.')
            return 1
        pause = min(interval,remaining)
        print('Waiting %.0fs before another read-only check; %.0fs polling time remains.' % (pause,remaining), flush=True)
        time.sleep(pause)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'probe':
            print(json.dumps(probe(*sys.argv[2:])))
        elif sys.argv[1] == 'run':
            sys.exit(run(*sys.argv[2:]))
        else:
            raise ValueError('Invalid DNS verification mode')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('FAIL: '+str(error), file=sys.stderr)
        sys.exit(2)
