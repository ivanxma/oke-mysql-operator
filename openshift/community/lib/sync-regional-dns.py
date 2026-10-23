"""Replicate selected cluster A sets across compartment-owned private DNS views."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
FIELDS=('OCI_REGION','OCI_COMPARTMENT_ID','OCI_COMPARTMENT_NAME','CLUSTER_NAME','BASE_DOMAIN','MACHINE_CIDR','NODE_IP','ADMIN_WORKDIR','OKD_STATE_DIR')


def require(value,message):
    if not value:raise ValueError(message)


def profile(path,region):
    require(path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o077,'Protected generated profile required')
    code='import os,json;print(json.dumps({k:os.environ.get(k, "") for k in '+repr(FIELDS)+'}))'
    script='set -e; source "$1" "$2"; python3 -c "$3"'
    data=json.loads(subprocess.check_output(['bash','-c',script,'--',str(ROOT/'openshift/community/regions'/region/'env.sh'),str(path),code],text=True,timeout=20))
    require(data['OCI_REGION']==region and all(data.values()),'Each selected profile needs a registered actual node IP and complete scope')
    return data


def checked_zone(zone,cid,view,domain):
    require(zone.compartment_id==cid and zone.view_id==view and zone.scope=='PRIVATE' and zone.zone_type=='PRIMARY'
            and zone.name.rstrip('.').lower()==domain.lower(),'Private zone scope/type/view/name differs; no record change')


def discover(values,oci):
    cid=values['OCI_COMPARTMENT_ID'];region=values['OCI_REGION'];address=values['NODE_IP']
    opts=dict(signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    config={'region':region};network=oci.core.VirtualNetworkClient(config,**opts);dns=oci.dns.DnsClient(config,**opts);compute=oci.core.ComputeClient(config,**opts)
    comp=oci.identity.IdentityClient(config,**opts).get_compartment(cid).data
    require(comp.name==values['OCI_COMPARTMENT_NAME'] and comp.lifecycle_state=='ACTIVE','Compartment identity mismatch')
    subnets=[s for s in oci.pagination.list_call_get_all_results(network.list_subnets,cid).data if s.compartment_id==cid and s.cidr_block==values['MACHINE_CIDR'] and s.prohibit_public_ip_on_vnic]
    require(len(subnets)==1,'Need one matching private machine subnet')
    subnet=subnets[0];vcn=network.get_vcn(subnet.vcn_id).data;require(vcn.compartment_id==cid,'VCN outside selected compartment')
    ips=oci.pagination.list_call_get_all_results(network.list_private_ips,subnet_id=subnet.id,ip_address=address).data
    require(len(ips)==1,'Registered target IP is not uniquely assigned')
    vnic=network.get_vnic(ips[0].vnic_id).data;require(vnic.compartment_id==cid and vnic.subnet_id==subnet.id and not vnic.public_ip,'Target VNIC scope mismatch')
    links=[a for a in oci.pagination.list_call_get_all_results(compute.list_vnic_attachments,cid).data if a.vnic_id==vnic.id and a.lifecycle_state=='ATTACHED']
    require(len(links)==1,'Target must identify one attached VM')
    instance=compute.get_instance(links[0].instance_id).data
    image=json.loads((Path(values['ADMIN_WORKDIR'])/'oci-control-plane-image.json').read_text())
    build=json.loads((Path(values['ADMIN_WORKDIR'])/'control-plane-build.json').read_text())
    require(build['cluster_domain']==values['CLUSTER_NAME']+'.'+values['BASE_DOMAIN'] and build['role']=='control-plane','Image build DNS identity differs from selected profile')
    require(instance.compartment_id==cid and instance.lifecycle_state=='RUNNING' and instance.image_id==image['image_id'] and image['compartment_id']==cid and image['region']==region,'Target VM/image/region/compartment mismatch')
    resolvers=[r for r in oci.pagination.list_call_get_all_results(dns.list_resolvers,compartment_id=cid).data if r.attached_vcn_id==vcn.id]
    require(len(resolvers)==1,'Need the target VCN private resolver')
    resolver=dns.get_resolver(resolvers[0].id,scope='PRIVATE').data
    view=dns.get_view(resolver.default_view_id).data
    require(resolver.compartment_id==cid and resolver.attached_vcn_id==vcn.id and view.compartment_id==cid,'Resolver/view scope mismatch')
    return dict(values,domain=values['CLUSTER_NAME']+'.'+values['BASE_DOMAIN'],view=view.id,dns=dns)


def synchronize(source,destination,action,oci,plan_records):
    dns=destination['dns'];cid=destination['OCI_COMPARTMENT_ID'];view=destination['view'];domain=source['domain']
    zones=oci.pagination.list_call_get_all_results(dns.list_zones,compartment_id=cid,scope='PRIVATE',view_id=view,name=domain).data
    require(len(zones)<=1,'Ambiguous private cluster zone')
    zone=dns.get_zone(zones[0].id,scope='PRIVATE').data if zones else None
    if zone:checked_zone(zone,cid,view,domain)
    print('PLAN '+destination['OCI_REGION']+': '+domain+' -> '+source['NODE_IP'],flush=True)
    if not zone:
        if action=='preview':return
        zone=dns.create_zone(oci.dns.models.CreateZoneDetails(compartment_id=cid,name=domain,zone_type='PRIMARY',scope='PRIVATE',view_id=view)).data
        checked_zone(zone,cid,view,domain)
    response=dns.get_zone_records(zone.id,scope='PRIVATE',limit=100)
    require(not response.headers.get('opc-next-page') and response.headers.get('etag'),'Record pagination or missing ETag requires review')
    changes=plan_records(response.data.items,domain,source['NODE_IP'])
    if changes and action=='apply':
        dns.patch_zone_records(zone.id,oci.dns.models.PatchZoneRecordsDetails(items=[oci.dns.models.RecordOperation(**x) for x in changes]),scope='PRIVATE',if_match=response.headers['etag'])
        after=dns.get_zone_records(zone.id,scope='PRIVATE',limit=100)
        require(not after.headers.get('opc-next-page') and not plan_records(after.data.items,domain,source['NODE_IP']),'DNS readback differs')


def main(args):
    selected,peer,action=args;require(action in ('preview','apply') and peer in ('uk-london-1','eu-frankfurt-1'),'Invalid selection')
    path=Path(selected);require(ROOT/'generated/openshift' in path.parents,'Run from a generated execution profile')
    current=path.parent.parent.name;require(current!=peer,'Select a different peer region')
    values=[profile(path,current),profile(ROOT/'generated/openshift'/peer/path.parent.name/'community.env',peer)]
    require(values[0]['OCI_COMPARTMENT_ID']==values[1]['OCI_COMPARTMENT_ID'],'This script is restricted to one selected compartment in the same tenancy')
    import oci
    sites=[discover(v,oci) for v in values];require(sites[0]['domain']!=sites[1]['domain'],'Cluster DNS identities must differ')
    spec=importlib.util.spec_from_file_location('cluster_dns_plan',Path(__file__).with_name('configure-dns.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # All resource/VM ownership checks above finish before the first DNS mutation.
    for destination in sites:
        for source in sites:synchronize(source,destination,action,oci,module.plan_records)
    print('Private cluster DNS copies verified in OCI. Resolver caches still need 05-verify-dns.sh. No resolver/routing changes.' if action=='apply' else 'Read-only preview; repeat with --apply after review.')


if __name__=='__main__':
    os.umask(0o077)
    try:main(sys.argv[1:])
    except Exception as error:sys.exit('Regional DNS sync stopped: '+str(error))
