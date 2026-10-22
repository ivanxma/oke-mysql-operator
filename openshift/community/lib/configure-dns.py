"""Apply three cluster A records without changing shared views/resolvers."""
import ipaddress
import json
from pathlib import Path
import sys


def plan_records(records, domain, address):
    ipaddress.IPv4Address(address)
    names = ['api.' + domain, 'api-int.' + domain, '*.apps.' + domain]
    operations = []
    for name in names:
        current = [r for r in records if r.domain.rstrip('.').lower() == name.lower()]
        if any(r.rtype.upper() == 'CNAME' for r in current):
            raise ValueError('Conflicting CNAME at ' + name + '; DNS owner must resolve it')
        addresses = [r for r in current if r.rtype.upper() == 'A']
        if len(addresses) == 1 and addresses[0].rdata == address:
            continue
        for record in addresses:
            operations.append(dict(operation='REMOVE', domain=name, rtype='A',
                                   rdata=record.rdata, ttl=record.ttl))
        operations.append(dict(operation='ADD', domain=name, rtype='A', rdata=address, ttl=60))
    return operations


def load_discovery(work, compartment_id, compartment_name, region, domain, address, machine):
    root = Path(work)
    path = root / 'oci-dns-view.json'
    if path.is_symlink():
        raise ValueError('Refusing symlink DNS discovery state')
    if not path.exists():
        return None
    saved = json.loads(path.read_text())
    expected = dict(admin_workdir=str(root), compartment_id=compartment_id, compartment_name=compartment_name,
                    region=region, cluster_domain=domain, node_ip=address, machine_cidr=machine)
    if any(saved.get(k) != v for k, v in expected.items()):
        raise ValueError('DNS discovery state does not match selected cluster/network/OCI scope; rerun discovery with the correct private profile')
    if not all(saved.get(k) for k in ('view_id','subnet_id','vcn_id','resolver_id')):
        raise ValueError('Incomplete DNS discovery state; rerun 05-discover-dns-view.sh')
    return saved


def configure(work, compartment_id, compartment_name, region, domain, address, action, view_id='', machine=''):
    import oci
    if action not in ('plan', 'apply'):
        raise ValueError('Expected plan/apply')
    saved = load_discovery(work, compartment_id, compartment_name, region, domain, address, machine)
    if saved:
        if view_id and view_id != saved['view_id']:
            raise ValueError('Explicit view conflicts with registered DNS discovery')
        view_id = saved['view_id']
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    options = dict(signer=signer, timeout=(10, 60), retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    compartment = oci.identity.IdentityClient(config, **options).get_compartment(compartment_id).data
    if compartment.id != compartment_id or compartment.name != compartment_name or compartment.lifecycle_state != 'ACTIVE':
        raise ValueError('Selected compartment identity/state mismatch')
    dns = oci.dns.DnsClient(config, **options)
    if saved:
        core = oci.core.VirtualNetworkClient(config, **options)
        subnet = core.get_subnet(saved['subnet_id']).data
        vcn = core.get_vcn(saved['vcn_id']).data
        resolver = dns.get_resolver(saved['resolver_id'], scope='PRIVATE').data
        if (subnet.compartment_id != compartment_id or subnet.vcn_id != saved['vcn_id'] or
                subnet.cidr_block != machine or subnet.lifecycle_state != 'AVAILABLE' or
                vcn.compartment_id != compartment_id or vcn.id != saved['vcn_id'] or
                resolver.compartment_id != compartment_id or resolver.attached_vcn_id != saved['vcn_id'] or
                resolver.default_view_id != view_id):
            raise ValueError('Live subnet/VCN/resolver no longer matches registered discovery; no DNS changes made')
        view = dns.get_view(view_id).data
        if view.compartment_id != compartment_id or view.id != view_id:
            raise ValueError('Registered view is outside selected compartment')
        print('Using registered DNS view; live subnet/VCN/resolver scope verified.')
    zones = oci.pagination.list_call_get_all_results(dns.list_zones, compartment_id,
                                                   scope='PRIVATE', name=domain).data
    matches = [z for z in zones if z.name.rstrip('.').lower() == domain.lower()]
    if not matches and view_id:
        view = dns.get_view(view_id).data
        if view.compartment_id != compartment_id or view.id != view_id:
            raise ValueError('Selected DNS view is outside authorized compartment')
        if action == 'plan':
            print('Plan: create private cluster zone ' + domain + ' in the supplied existing view, then add the three A records.')
            print('No change made. Run 05-configure-dns.sh --apply after reviewing resolver visibility.' if saved else
                  'No change made. Run with the same --view-id and --apply after reviewing resolver visibility.')
            return
        created = dns.create_zone(oci.dns.models.CreateZoneDetails(
            compartment_id=compartment_id, name=domain, zone_type='PRIMARY',
            scope='PRIVATE', view_id=view_id)).data
        matches = [created]
    if len(matches) != 1:
        raise ValueError('Need exactly one existing private zone for ' + domain +
                         ' in selected compartment/region. Run 05-discover-dns-view.sh to register the target view, then rerun this step. Explicit --view-id remains optional for a reviewed manual view. The view must be visible to admin and node resolvers; this script does not change shared networking.')
    zone = dns.get_zone(matches[0].id, scope='PRIVATE').data
    if zone.compartment_id != compartment_id or zone.scope != 'PRIVATE' or zone.zone_type != 'PRIMARY' or zone.name.rstrip('.').lower() != domain.lower():
        raise ValueError('Private zone scope/type/name mismatch')
    if view_id and zone.view_id != view_id:
        raise ValueError('Existing private zone belongs to a different view')
    response = dns.get_zone_records(zone.id, scope='PRIVATE', limit=100)
    if response.headers.get('opc-next-page'):
        raise ValueError('Zone exceeds one page; DNS owner review required')
    etag = response.headers.get('etag')
    if not etag:
        raise ValueError('No DNS records ETag; refusing unguarded update')
    operations = plan_records(response.data.items, domain, address)
    for prefix in ('api.', 'api-int.', '*.apps.'):
        print(prefix + domain + ' A ' + address, flush=True)
    if not operations:
        print('DNS records already match; no mutation needed.')
    elif action == 'plan':
        print('Read-only plan. Run again with --apply to update only these three A record sets.')
    else:
        dns.patch_zone_records(zone.id, oci.dns.models.PatchZoneRecordsDetails(items=[
            oci.dns.models.RecordOperation(**op) for op in operations]), scope='PRIVATE', if_match=etag)
        after = dns.get_zone_records(zone.id, scope='PRIVATE', limit=100)
        if after.headers.get('opc-next-page') or plan_records(after.data.items, domain, address):
            raise ValueError('DNS readback differs; inspect private zone')
        print('Private DNS records verified in OCI. Resolver visibility still needs validation.')
    print('Next: run 00-validate-environment.sh --existing-target to check resolution and target networking.')


def main(arguments):
    import oci
    try:
        configure(*arguments)
    except oci.exceptions.ServiceError as error:
        sys.stdout.flush()
        operation = getattr(error, 'operation_name', None) or 'DNS request'
        print('FAIL: OCI %s: HTTP %s / %s.' %
              (operation, error.status, error.code), file=sys.stderr)
        if error.status == 400:
            print('OCI parameter error: ' + str(getattr(error, 'message', 'See the private OCI request ID.')), file=sys.stderr)
            print('No IAM broadening is indicated by this parameter error. Keep existing installer and discovery state; correct the request and rerun.', file=sys.stderr)
        elif error.status in (403, 404) and operation == 'create_zone':
            print('Private zone creation was denied or the selected resource is unavailable. '
                  'Discovery and preview succeeded; they do not prove creation permission.', file=sys.stderr)
            print('CreateZone requires DNS_ZONE_CREATE and DNS_VIEW_INSPECT for private zones. '
                  'Verify an effective manage dns-zones grant in the selected compartment '
                  '(use dns-zones does not include creation), plus read dns-views for the verified view. '
                  'Keep use dns-records for the subsequent record updates.', file=sys.stderr)
            print('After policy propagation, retry 05-configure-dns.sh --apply. '
                  'No image rebuild or repeated view entry is needed. '
                  'If these grants already exist, have the IAM administrator check principal matching, '
                  'policy conditions and the saved OCI request ID. No authentication/scope fallback.', file=sys.stderr)
        elif error.status in (403, 404):
            print('Authorization denied or resource unavailable in the selected region/compartment. '
                  'A denied ListZones does not prove the zone is absent.', file=sys.stderr)
            print('Required: compartment-scoped inspect dns-zones for discovery; '
                  'use dns-zones and use dns-records restricted to the selected cluster zone '
                  'for GetZoneRecords/PatchZoneRecords. A zone-name condition alone may not '
                  'authorize compartment-level ListZones.', file=sys.stderr)
            print('Optional zone creation additionally needs DNS_ZONE_CREATE and read dns-views '
                  'for the supplied existing view. See the private regional HTML guide for '
                  'deployment-specific policy statements. No credentials or scope will be changed.', file=sys.stderr)
        request_id = getattr(error, 'request_id', None)
        if request_id:
            print('OCI request ID: ' + str(request_id), file=sys.stderr)
        return 1
    except ValueError as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
