"""Read-only mapping from the configured node subnet to its DNS default view."""
import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile


def discover(work, compartment_id, compartment_name, region, machine, address, domain):
    import oci
    root = Path(work)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError('Expected existing absolute admin workspace')
    scope = dict(admin_workdir=str(root), compartment_id=compartment_id, compartment_name=compartment_name,
                 region=region, machine_cidr=machine, node_ip=address, cluster_domain=domain)
    receipt = root / 'oci-dns-view.json'
    if receipt.is_symlink():
        raise ValueError('Refusing symlink discovery state')
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if any(saved.get(k) != v for k, v in scope.items()):
            raise ValueError('Saved DNS discovery belongs to another workspace/cluster/network/OCI scope')
    network = ipaddress.IPv4Network(machine)
    if ipaddress.IPv4Address(address) not in network:
        raise ValueError('Registered node is outside configured machine subnet')
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    options = dict(signer=signer, timeout=(10, 60), retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    compartment = oci.identity.IdentityClient(config, **options).get_compartment(compartment_id).data
    if compartment.id != compartment_id or compartment.name != compartment_name or compartment.lifecycle_state != 'ACTIVE':
        raise ValueError('Compartment identity/state mismatch')
    core = oci.core.VirtualNetworkClient(config, **options)
    subnets = oci.pagination.list_call_get_all_results(core.list_subnets, compartment_id).data
    matches = [s for s in subnets if s.compartment_id == compartment_id
               and s.lifecycle_state == 'AVAILABLE' and s.cidr_block == str(network)]
    if len(matches) != 1:
        raise ValueError('Need exactly one AVAILABLE matching machine subnet in selected compartment/region; DNS owner must review ambiguous/missing inventory')
    subnet = matches[0]
    vcn = core.get_vcn(subnet.vcn_id).data
    if vcn.compartment_id != compartment_id or vcn.id != subnet.vcn_id:
        raise ValueError('Target VCN is outside selected compartment')
    dns = oci.dns.DnsClient(config, **options)
    resolvers = oci.pagination.list_call_get_all_results(dns.list_resolvers, compartment_id, scope='PRIVATE').data
    matches = [r for r in resolvers if r.attached_vcn_id == vcn.id and r.compartment_id == compartment_id]
    if len(matches) != 1:
        raise ValueError('Need exactly one private resolver attached to target VCN; ask DNS owner to inspect it')
    resolver = dns.get_resolver(matches[0].id, scope='PRIVATE').data
    if resolver.compartment_id != compartment_id or resolver.attached_vcn_id != vcn.id:
        raise ValueError('Resolver scope mismatch')
    view = dns.get_view(resolver.default_view_id).data
    if view.compartment_id != compartment_id or view.id != resolver.default_view_id:
        raise ValueError('Default DNS view is outside selected compartment')
    record = dict(scope, subnet_id=subnet.id, vcn_id=vcn.id, resolver_id=resolver.id, view_id=view.id)
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.dns-view-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream, indent=2)
            stream.write('\n')
            stream.flush()
            temporary.replace(receipt)
        finally:
            temporary.unlink(missing_ok=True)
    print('Read-only discovery; no DNS/network resources changed.')
    print('Matched subnet: ' + subnet.id)
    print('Target VCN: ' + vcn.id)
    print('Default private DNS view: ' + view.id)
    print('This view belongs to the target VCN resolver. Admin/custom DHCP/forwarding visibility still needs validation.')
    print('Private discovery state registered: ' + str(receipt))
    print('The configuration script loads and revalidates this state automatically; no copy/paste or env reload needed.')
    command = './regions/' + region + '/05-configure-dns.sh'
    print('Preview zone creation:\n' + command)
    print('After review, create zone and records:\n' + command + ' --apply')
    return view.id


def main(arguments):
    import oci
    try:
        discover(*arguments)
    except oci.exceptions.ServiceError as error:
        print('FAIL: OCI discovery %s: HTTP %s / %s. Check read virtual-network-family, read dns-resolvers and read dns-views in the selected compartment. No authentication/scope fallback.' %
              (getattr(error, 'operation_name', None) or 'read request', error.status, error.code), file=sys.stderr)
        return 1
    except ValueError as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
