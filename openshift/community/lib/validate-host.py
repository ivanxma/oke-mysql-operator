#!/usr/bin/env python3
"""Read-only remote checks; JSON input/output, standard library only."""
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request


def run(args, timeout=15):
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ''


def ip_in(address, cidr):
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def rule_matches(rule, direction, protocol, port, source_ip, source_nsgs=()):
    """Match OCI CIDR/NSG rules. None means a possible stateless allowance."""
    if rule.get('direction', direction).upper() != direction:
        return False
    if str(rule.get('protocol')) not in ('all', str(protocol)):
        return False
    field = 'source' if direction == 'INGRESS' else 'destination'
    kind = rule.get(field + '-type', 'CIDR_BLOCK')
    if kind == 'CIDR_BLOCK':
        if not ip_in(source_ip, rule.get(field, '')):
            return False
    elif kind == 'NETWORK_SECURITY_GROUP':
        if rule.get(field) not in source_nsgs:
            return False
    else:
        return False
    options = rule.get('tcp-options' if str(protocol) == '6' else 'udp-options') or {}
    ports = options.get('destination-port-range')
    if ports and not int(ports['min']) <= port <= int(ports['max']):
        return False
    # A restricted source-port range is not proven for arbitrary client sockets.
    if options.get('source-port-range') or rule.get('is-stateless', False):
        return None
    return True


class Report:
    def __init__(self, role):
        self.role, self.checks, self.facts = role, [], {}

    def add(self, status, check, detail):
        self.checks.append(dict(status=status, check=check, detail=detail))

    def https(self, name, url):
        if not shutil.which('curl'):
            self.add('WARN', name, 'curl missing; run step 01, then repeat validation.')
            return
        rc, code = run(['curl', '--silent', '--show-error', '--head', '--location',
                        '--connect-timeout', '5', '--max-time', '15', '--output', os.devnull,
                        '--write-out', '%{http_code}', url], timeout=18)
        if not rc and (code.startswith(('2', '3')) or (name.endswith('registry') and code == '401')):
            self.add('PASS', name, 'HTTPS endpoint reachable (HTTP ' + code + ').')
        else:
            self.add('FAIL', name, 'HTTPS check failed (HTTP ' + (code or 'unavailable') + '); check DNS, NAT/proxy, TLS trust and firewall allowlist.')

    def emit(self):
        print(json.dumps(dict(role=self.role, checks=self.checks, facts=self.facts)))


def metadata(path):
    # OCI link-local metadata is not sent through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request('http://169.254.169.254/opc/v2/' + path,
                                 headers={'Authorization': 'Bearer Oracle'})
    try:
        with opener.open(req, timeout=3) as response:
            return json.load(response)
    except (OSError, ValueError):
        return {}


def host_checks(report, cfg):
    release = {}
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                release[k] = v.strip('"')
    except OSError:
        pass
    is_ol9 = release.get('ID') == 'ol' and release.get('VERSION_ID', '').split('.')[0] == '9'
    coreos = release.get('ID') in ('scos', 'fcos', 'rhcos') or release.get('VARIANT_ID') == 'coreos'
    valid_os = is_ol9 if report.role == 'admin' else is_ol9 or coreos
    report.add('PASS' if valid_os else 'FAIL', 'Host OS',
               release.get('PRETTY_NAME', 'Unknown OS') + '; admin requires OL9; existing target inventory permits OL9 or CoreOS.')
    report.add('PASS' if platform.machine() == 'x86_64' else 'FAIL', 'CPU architecture', platform.machine() + '; x86_64 tools/image are selected.')
    instance, vnics = metadata('instance/'), metadata('vnics/')
    if isinstance(vnics, list):
        vnic = next((v for v in vnics if v.get('privateIp') == cfg.get('NODE_IP')), vnics[0] if vnics else {})
    else:
        vnic = {}
    report.facts.update(instance_id=instance.get('id'), region=instance.get('region'),
                        vnic_id=vnic.get('vnicId'), private_ip=vnic.get('privateIp'),
                        subnet_cidr=vnic.get('subnetCidrBlock'))
    rc, synced = run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'])
    report.add('PASS' if rc == 0 and synced == 'yes' else 'WARN', 'Time synchronization',
               'NTP synchronized.' if synced == 'yes' else 'Confirm chrony/NTP and UDP 123; step 01 enables chronyd on the admin host.')
    if report.role == 'target':
        cpus = os.cpu_count() or 0
        mem = re.search(r'^MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)
        gib = int(mem[1]) / 1024 / 1024 if mem else 0
        report.add('PASS' if cpus >= 4 else 'FAIL', 'Target CPU', str(cpus) + ' vCPUs; minimum 4, 8+ recommended.')
        # /proc/meminfo excludes reserved firmware/kernel memory.
        report.add('PASS' if gib >= 15 else 'FAIL', 'Target memory', '%.1f GiB usable; minimum nominal 16 GB, additional headroom recommended.' % gib)
        disk = cfg.get('INSTALL_DISK', '')
        rc, size = run(['lsblk', '-bdn', '-o', 'SIZE', disk])
        size_bytes = int(size) if not rc and size.isdigit() else 0
        report.add('PASS' if size_bytes >= 120_000_000_000 else 'FAIL', 'Installation disk',
                   ('%.1f GB detected; minimum 120 GB.' % (size_bytes / 1e9)) if size_bytes else 'Configured installation disk is missing or unreadable; verify INSTALL_DISK.')
        report.add('PASS' if Path('/sys/firmware/efi').exists() else 'FAIL', 'Firmware',
                   'UEFI detected.' if Path('/sys/firmware/efi').exists() else 'Configure UEFI for the OCI custom image and instance.')
        report.add('PASS' if vnic.get('privateIp') == cfg.get('NODE_IP') else 'WARN', 'Target private IP',
                   'Metadata agrees with configured NODE_IP.' if vnic.get('privateIp') == cfg.get('NODE_IP') else 'Cannot confirm configured target IP through OCI metadata; review primary VNIC.')
        if vnic.get('subnetCidrBlock'):
            try:
                matches = ipaddress.ip_network(vnic['subnetCidrBlock']) == ipaddress.ip_network(cfg['MACHINE_CIDR'])
            except (KeyError, ValueError):
                matches = False
            report.add('PASS' if matches else 'FAIL', 'Target subnet', 'Observed subnet matches MACHINE_CIDR.' if matches else 'MACHINE_CIDR does not match the observed OCI subnet.')
        report.add('ACTION', 'OS installation', 'Use CoreOS for cluster nodes. This inventory does not authorize disk writes or establish cluster readiness; use step 06 after installation.')
    else:
        work = Path(cfg['ADMIN_WORKDIR'])
        parent = work
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        available = shutil.disk_usage(parent).free / 1024**3
        report.add('FAIL' if available < 10 else 'WARN' if available < 30 else 'PASS', 'Admin free disk',
                   '%.1f GiB free; need at least 10 GiB, recommend 30 GiB for images and container storage.' % available)
        rc, _ = run(['sudo', '-n', 'true'])
        report.add('PASS' if not rc else 'FAIL', 'Admin sudo', 'Passwordless sudo available.' if not rc else 'Step 01 requires passwordless sudo for packages.')
        missing = [x for x in ('curl', 'wget', 'jq', 'podman', 'gpg', 'dig', 'chronyc') if not shutil.which(x)]
        report.add('WARN' if missing else 'PASS', 'Preparation utilities',
                   'Missing: ' + ', '.join(missing) + '. Run step 01.' if missing else 'Preparation utilities are available.')
    report.https('Public release downloads', 'https://github.com/okd-project/okd/releases/download/' + cfg['OKD_VERSION'] + '/sha256sum.txt')
    report.https('Community container registry', 'https://quay.io/v2/')
    if report.role == 'admin':
        report.https('OKD signing-key download', 'https://www.okd.io/okd-maintainers.pub')
    domain = cfg.get('BASE_DOMAIN', '')
    if not domain:
        report.add('FAIL', 'Cluster DNS configuration', 'Set BASE_DOMAIN in the private config, then create api, api-int and wildcard apps A records.')
    else:
        base = cfg['CLUSTER_NAME'] + '.' + domain
        for name in ('api.' + base, 'api-int.' + base, 'console-openshift-console.apps.' + base):
            try:
                addresses = {i[4][0] for i in socket.getaddrinfo(name, None, socket.AF_INET)}
            except OSError:
                addresses = set()
            report.add('PASS' if cfg.get('NODE_IP') in addresses else 'FAIL', 'DNS: ' + name,
                       'Resolves to the configured node.' if cfg.get('NODE_IP') in addresses else 'Missing/wrong A record or private DNS forwarding; expected NODE_IP.')
    try:
        resolvers = re.findall(r'^nameserver\s+(\S+)', Path('/etc/resolv.conf').read_text(), re.M)
    except OSError:
        resolvers = []
    report.add('PASS' if resolvers else 'FAIL', 'DNS resolver', 'Resolver configuration present; DNS queries above test private-zone visibility.' if resolvers else 'Configure DHCP/resolver settings; no DNS nameserver found.')
    report.facts['resolvers'] = resolvers


def cloud_checks(report, cfg, target):
    """Inspect live OCI effective subnet/NSG allow rules using read-only commands."""
    if not shutil.which('oci'):
        report.add('WARN', 'OCI network audit', 'OCI CLI unavailable on admin. Install/configure it or review subnet routes, security lists, NSGs and DHCP manually.')
        return
    vnic_id = target.get('vnic_id')
    if not vnic_id:
        report.add('WARN', 'OCI network audit', 'Target VNIC metadata unavailable; supply/verify OCI inventory before the cloud-rule audit.')
        return
    cmd = ['oci', '--auth', cfg.get('OCI_AUTH', 'instance_principal'), '--connection-timeout', '5',
           '--read-timeout', '10', '--max-retries', '0']
    region = cfg.get('OCI_REGION') or target.get('region')
    if region:
        cmd += ['--region', region]

    def get(args, region_override=None):
        regional_cmd = list(cmd)
        if region_override:
            if '--region' in regional_cmd:
                regional_cmd[regional_cmd.index('--region') + 1] = region_override
            else:
                regional_cmd += ['--region', region_override]
        rc, output = run(regional_cmd + args, timeout=20)
        if rc:
            raise RuntimeError('OCI read failed; verify authentication and read permissions for networking resources.')
        return json.loads(output)['data']

    try:
        vnic = get(['network', 'vnic', 'get', '--vnic-id', vnic_id])
        subnet = get(['network', 'subnet', 'get', '--subnet-id', vnic['subnet-id']])
        report.add('PASS' if not vnic.get('public-ip') else 'FAIL', 'Target public exposure',
                   'Target VNIC has no public IP.' if not vnic.get('public-ip') else 'Remove the control-plane public IP for this private design.')
        report.add('PASS' if subnet.get('prohibit-public-ip-on-vnic') else 'WARN', 'Private subnet policy',
                   'Subnet prohibits public IPs.' if subnet.get('prohibit-public-ip-on-vnic') else 'Subnet permits public IPs. Keep the target without one; use a private subnet for the intended design.')
        vcn = get(['network', 'vcn', 'get', '--vcn-id', subnet['vcn-id']])
        vcn_ranges = vcn.get('cidr-blocks') or [vcn.get('cidr-block')]
        overlaps = any(ipaddress.ip_network(a).overlaps(ipaddress.ip_network(b))
                       for a in vcn_ranges if a for b in (cfg['POD_CIDR'], cfg['SERVICE_CIDR']))
        report.add('FAIL' if overlaps else 'PASS', 'VCN / overlay overlap',
                   'VCN overlaps the pod/service range; change the install inputs.' if overlaps else 'VCN address ranges do not overlap configured pod/service CIDRs.')
        rt = get(['network', 'route-table', 'get', '--rt-id', subnet['route-table-id']])
        defaults = [r for r in rt.get('route-rules', []) if r.get('destination') == '0.0.0.0/0']
        if defaults:
            hop = defaults[0].get('network-entity-id', '')
            if hop.startswith('ocid1.natgateway.'):
                nat = get(['network', 'nat-gateway', 'get', '--nat-gateway-id', hop])
                good = nat.get('lifecycle-state') == 'AVAILABLE' and not nat.get('block-traffic', False)
                report.add('PASS' if good else 'FAIL', 'Private internet route',
                           'Default route uses an available NAT Gateway.' if good else 'NAT Gateway is unavailable or traffic is blocked.')
            elif hop.startswith('ocid1.internetgateway.') and not vnic.get('public-ip'):
                report.add('FAIL', 'Private internet route', 'A private VNIC cannot use an Internet Gateway alone; provide NAT or approved proxy/firewall egress.')
            else:
                report.add('WARN', 'Private internet route', 'Default route uses another next hop; confirm DRG/firewall/proxy routing and return paths. HTTPS probes are separate evidence.')
        else:
            report.add('WARN', 'Private internet route', 'No default route found. Confirm approved proxy or specific egress routes; private nodes still need release/registry access.')
        dhcp = get(['network', 'dhcp-options', 'get', '--dhcp-id', subnet['dhcp-options-id']])
        report.add('PASS' if dhcp.get('options') else 'WARN', 'OCI DHCP options',
                   'DHCP options read. Validate cluster-zone resolution using the DNS checks from both hosts.' if dhcp.get('options') else 'No DHCP options returned; verify resolver configuration.')
        rules, complete = [], True
        for sl in subnet.get('security-list-ids', []):
            try:
                data = get(['network', 'security-list', 'get', '--security-list-id', sl])
                for key, direction in (('ingress-security-rules', 'INGRESS'), ('egress-security-rules', 'EGRESS')):
                    rules += [dict(r, direction=direction) for r in data.get(key, [])]
            except (RuntimeError, ValueError):
                complete = False
        for nsg in vnic.get('nsg-ids', []):
            try:
                rules += get(['network', 'nsg', 'rules', 'list', '--nsg-id', nsg, '--all'])
            except (RuntimeError, ValueError):
                complete = False
        admin_ip, admin_nsgs, admin_groups_known = None, [], False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect((cfg['NODE_IP'], 22))
            admin_ip = sock.getsockname()[0]
            sock.close()
            own_vnic_id = report.facts.get('vnic_id')
            if own_vnic_id:
                own_vnic = get(['network', 'vnic', 'get', '--vnic-id', own_vnic_id], report.facts.get('region'))
                admin_nsgs = own_vnic.get('nsg-ids', [])
                admin_groups_known = True
        except (OSError, RuntimeError, ValueError):
            pass

        def flow(name, protocol, port, ip, groups=(), group_known=True):
            found = [rule_matches(r, 'INGRESS', protocol, port, ip, groups) for r in rules]
            if any(x is True for x in found):
                report.add('PASS', name, 'A matching stateful ingress allow exists in the combined security lists/NSGs.')
            elif any(x is None for x in found):
                report.add('WARN', name, 'A matching rule is stateless or source-port restricted; validate both directions and return ports.')
            elif complete and group_known:
                report.add('FAIL', name, 'No matching ingress rule. Add a stateful rule restricted to the required source CIDR/NSG.')
            else:
                report.add('WARN', name, 'Cannot prove an allowance because some security rules or source NSG memberships are unreadable.')
        if admin_ip:
            for port in (22, 443, 6443):
                flow('Admin to target TCP ' + str(port), 6, port, admin_ip, admin_nsgs, admin_groups_known)
        else:
            report.add('WARN', 'Admin ingress sources', 'Unable to determine the private source IP/NSGs of the jump-to-target path.')
        # In cross-VCN deployments, inspect both subnet route tables in their
        # own regions. A NAT/Internet Gateway is not private return connectivity.
        if admin_ip and admin_groups_known:
            try:
                own_region = report.facts.get('region')
                own_subnet = get(['network', 'subnet', 'get', '--subnet-id', own_vnic['subnet-id']], own_region)
                if own_subnet['vcn-id'] != subnet['vcn-id']:
                    own_rt = get(['network', 'route-table', 'get', '--rt-id', own_subnet['route-table-id']], own_region)
                    for label, table, destination in (
                        ('Jump subnet to target', own_rt, cfg['NODE_IP']),
                        ('Target subnet return to jump', rt, admin_ip)):
                        matching = []
                        for route in table.get('route-rules', []):
                            try:
                                net = ipaddress.ip_network(route.get('destination', ''))
                                if ipaddress.ip_address(destination) in net:
                                    matching.append((net.prefixlen, route))
                            except ValueError:
                                continue
                        matching.sort(key=lambda item: item[0], reverse=True)
                        hop = matching[0][1].get('network-entity-id', '') if matching else ''
                        if hop.startswith(('ocid1.drg.', 'ocid1.localpeeringgateway.')):
                            report.add('PASS', label, 'Longest-prefix private route uses DRG/peering. Live SSH separately proves a working bidirectional path.')
                        elif hop.startswith(('ocid1.natgateway.', 'ocid1.internetgateway.')) or not hop:
                            report.add('FAIL', label, 'No suitable private route: configure the DRG/peering route and return path without replacing existing unrelated routes.')
                        else:
                            report.add('WARN', label, 'Private traffic uses another next hop; validate the network appliance, DRG attachment/RPC routes and return path.')
                    report.add('PASS', 'Regional network selection', 'Admin and target network resources were read through their respective regional OCI endpoints.')
            except (RuntimeError, ValueError, KeyError):
                report.add('WARN', 'Cross-VCN route audit', 'Cannot read both subnet route tables. Verify DRG attachments/RPC peering, both directions of routing, and security in each region.')
        clients = cfg.get('CLIENT_CIDR', '')
        if clients:
            client = ipaddress.ip_network(clients, strict=False)
            # Verify the whole configured client CIDR, not only one sample address.
            for port in (443, 6443):
                covering = [r for r in rules if r.get('direction') == 'INGRESS' and r.get('source-type', 'CIDR_BLOCK') == 'CIDR_BLOCK'
                            and ip_in(str(client.network_address), r.get('source', ''))
                            and ip_in(str(client.broadcast_address), r.get('source', ''))]
                found = [rule_matches(r, 'INGRESS', 6, port, str(client.network_address)) for r in covering]
                report.add('PASS' if any(x is True for x in found) else 'WARN' if any(x is None for x in found) or not complete else 'FAIL',
                           'Client CIDR to TCP ' + str(port),
                           'A stateful rule covers the configured client CIDR.' if any(x is True for x in found)
                           else 'No proven stateful allowance for the full CLIENT_CIDR. Confirm this CIDR, routing, return path and target rules.')
        else:
            report.add('WARN', 'Corporate browser/API access', 'Set CLIENT_CIDR in private config to audit TCP 443/6443 ingress. Also confirm corporate private routes and DNS forwarding.')
        for port in (443,):
            found = [rule_matches(r, 'EGRESS', 6, port, '192.0.2.1') for r in rules
                     if r.get('destination') == '0.0.0.0/0']
            report.add('PASS' if any(x is True for x in found) else 'WARN', 'Target HTTPS egress rules',
                       'Stateful TCP 443 egress to all internet destinations is allowed.' if any(x is True for x in found)
                       else 'No broad stateful HTTPS egress rule found; review restricted registry/CDN/proxy allowances. Live HTTPS probes test current endpoints only.')
        unsafe = []
        for port in (2379, 2380, 22623, 22624):
            if any(rule_matches(r, 'INGRESS', 6, port, '192.0.2.1') is not False for r in rules
                   if r.get('source') == '0.0.0.0/0'):
                unsafe.append(str(port))
        report.add('FAIL' if unsafe else 'PASS' if complete else 'WARN', 'Sensitive internal ports',
                   'World-source rules allow internal ports: ' + ', '.join(unsafe) + '. Restrict these to required private sources.' if unsafe
                   else 'No world-source etcd/Machine Config Server allowance found.' if complete else 'Not all rules are readable; verify internal ports are private.')
        if not complete:
            report.add('WARN', 'Security-rule visibility', 'Audit incomplete: grant read access to all attached security lists/NSGs and repeat. NSGs do not narrow a security-list allowance.')
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        report.add('WARN', 'OCI network audit', str(exc) + ' Routes, security rules and DHCP are not fully validated.')
    report.add('WARN', 'External network controls', 'OCI rule reads cannot prove corporate routes, DNS forwarding, proxy rules, packet MTU or all CoreOS host firewall behavior. Verify those paths before acceptance.')


def main():
    cfg, role = json.loads(sys.argv[1]), sys.argv[2]
    report = Report(role)
    host_checks(report, cfg)
    if role == 'admin':
        if cfg.get('PLANNED_TARGET') != 'true':
            try:
                with socket.create_connection((cfg['NODE_IP'], 22), timeout=5):
                    report.add('PASS', 'Jump to target SSH port', 'Private target TCP 22 is reachable.')
            except OSError:
                report.add('FAIL', 'Jump to target SSH port', 'No private TCP 22 connection; check VCN/DRG routes, return routing, security lists/NSGs and target SSH service.')
        target = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        cloud_checks(report, cfg, target)
    report.emit()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'role': sys.argv[2] if len(sys.argv)>2 else 'unknown', 'checks': [
            {'status': 'FAIL', 'check': 'Validator execution', 'detail': str(exc)}], 'facts': {}}))
        sys.exit(1)
