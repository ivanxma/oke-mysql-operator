"""Compartment-owned, gateway-only NSGs; never modify shared lists or routes."""
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sys
from workflow import Workflow, require, private_write


def peer_rules(address):
    """Permit only exact peer private /32, stateful private IPsec and ICMP echo."""
    source=str(ipaddress.IPv4Address(address))+'/32'
    rules=[]
    for direction in ('INGRESS','EGRESS'):
        field='source' if direction=='INGRESS' else 'destination'
        for protocol in ('17','50','1'):
            r=dict(direction=direction,protocol=protocol,is_stateless=False,
                   description='Submariner private peer gateway',**{field:source,field+'_type':'CIDR_BLOCK'})
            if protocol=='17':r['udp_options']=dict(destination_port_range=dict(min=4500,max=4500))
            if protocol=='1':r['icmp_options']=dict(type=8)
            rules.append(r)
    return rules


def matching(actual, expected):
    # Ignore server-generated IDs, timestamps and null options, but compare scope exactly.
    for key,value in expected.items():
        if key=='description':continue
        current=actual.get(key)
        if isinstance(value,dict):
            if not isinstance(current,dict) or not matching(current,value):return False
        elif current!=value:return False
    if expected.get('protocol')=='17' and actual.get('udp_options',{}).get('source_port_range'):return False
    if expected.get('protocol')=='1' and actual.get('icmp_options',{}).get('code') is not None:return False
    return True


def owned(group,cid,vcn,uid,peer_uid):
    require(group.compartment_id==cid and group.vcn_id==vcn and group.lifecycle_state=='AVAILABLE' and
            (group.freeform_tags or {}).get('okd_cluster_uid')==uid and
            (group.freeform_tags or {}).get('submariner_peer_uid')==peer_uid,
            'Gateway NSG ownership/compartment/VCN mismatch; no changes.')


def route_to_peer(rules,address):
    matches=[r for r in rules if r.destination_type=='CIDR_BLOCK' and ipaddress.ip_address(address) in ipaddress.ip_network(r.destination)]
    require(bool(matches),'No route toward peer gateway; ask network owner, no route change.')
    prefix=max(ipaddress.ip_network(r.destination).prefixlen for r in matches)
    best=[r for r in matches if ipaddress.ip_network(r.destination).prefixlen==prefix]
    require(all(r.network_entity_id.startswith('ocid1.drg.') for r in best),'Peer gateway route does not use a DRG; no routing changes.')


def main(action):
    require(action in ('preview','apply'),'Use preview or apply.')
    w=Workflow();w.peer_allowed();info=w.validation()
    root=Path(__file__).resolve().parents[5]
    spec=importlib.util.spec_from_file_location('profile_reader',root/'openshift/community/lib/sync-regional-dns.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    selected=Path(w.env['OKD_CONFIG_FILE']);peer=json.loads((w.state/'peer-registration.json').read_text())
    require(peer['uid']==info['peer']['uid'],'Peer discovery identity changed.')
    regions=('uk-london-1',peer['region']);roles=('target','peer');sites=[]
    import oci
    allr=oci.pagination.list_call_get_all_results
    opts=dict(signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    for region,role in zip(regions,roles):
        values=m.profile(root/'generated/openshift'/region/selected.parent.name/'community.env',region)
        cid=values['OCI_COMPARTMENT_ID'];require(cid==w.env['OCI_COMPARTMENT_ID'],'Both gateways must be in the selected compartment.')
        item=info[role];node=w.read(role=='peer','get','node',item['gateway'])
        address=next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')
        state=Path(values['OKD_STATE_DIR']);records=json.loads((state/'worker-nodes.json').read_text())
        require(records['cluster_uid']==item['uid'],'Worker registration belongs to another cluster.')
        records=[(iid,r) for iid,r in records['workers'].items() if r['name']==item['gateway'] and r['address']==address and r['node_uid']==node['metadata']['uid']]
        require(len(records)==1,'Gateway must be enrolled through step 13 with matching node identity.')
        net=oci.core.VirtualNetworkClient({'region':region},**opts);compute=oci.core.ComputeClient({'region':region},**opts)
        comp=oci.identity.IdentityClient({'region':region},**opts).get_compartment(cid).data
        require(comp.name==values['OCI_COMPARTMENT_NAME'] and comp.lifecycle_state=='ACTIVE','Compartment identity mismatch.')
        image=json.loads((Path(values['ADMIN_WORKDIR'])/'worker-image/oci-worker-image.json').read_text())
        instance=compute.get_instance(records[0][0]).data
        require(instance.compartment_id==cid and instance.lifecycle_state=='RUNNING' and instance.image_id==image['image_id'] and image['compartment_id']==cid and image['region']==region,'Gateway VM/image/scope mismatch.')
        bindings=[a for a in allr(compute.list_vnic_attachments,cid,instance_id=instance.id).data if a.lifecycle_state=='ATTACHED']
        vnics=[net.get_vnic(a.vnic_id).data for a in bindings]
        vnics=[v for v in vnics if v.private_ip==address and v.compartment_id==cid and not v.public_ip]
        require(len(vnics)==1,'Gateway VNIC identity is not unique/private.')
        vnic=vnics[0];subnet=net.get_subnet(vnic.subnet_id).data
        require(subnet.compartment_id==cid and subnet.cidr_block==values['MACHINE_CIDR'],'Gateway subnet scope mismatch.')
        vcn=net.get_vcn(subnet.vcn_id).data;require(vcn.compartment_id==cid,'VCN outside selected compartment.')
        rt=net.get_route_table(subnet.route_table_id).data;require(rt.compartment_id==cid,'Route table outside selected compartment.')
        sites.append(dict(values=values,cid=cid,item=item,address=address,net=net,vnic=vnic,subnet=subnet,vcn=vcn,routes=rt.route_rules))
    for n,s in enumerate(sites):
        other=sites[1-n];route_to_peer(s['routes'],other['address'])
        name='submariner-gateway-'+s['item']['uid'][:8]
        groups=[g for g in allr(s['net'].list_network_security_groups,compartment_id=s['cid'],vcn_id=s['vcn'].id).data if g.display_name==name]
        require(len(groups)<=1,'Ambiguous gateway NSG; preserve existing resources.')
        group=groups[0] if groups else None
        if group:owned(group,s['cid'],s['vcn'].id,s['item']['uid'],other['item']['uid'])
        require(len(s['vnic'].nsg_ids or [])+(0 if group and group.id in (s['vnic'].nsg_ids or []) else 1)<=5,'Gateway VNIC NSG limit would be exceeded.')
        s.update(group=group,name=name,peer_uid=other['item']['uid'],rules=peer_rules(other['address']))
        print('PLAN '+s['values']['OCI_REGION']+': '+name+' on selected worker only; exact peer /32 UDP4500, ESP50, ICMP echo. Existing DRG route read; shared networking unchanged.',flush=True)
    if action=='preview':print('Read-only preview; use --apply to create and attach dedicated gateway NSGs.');return
    # Both complete identities/routes/membership capacity validated before mutation.
    receipt=[]
    for s in sites:
        net=s['net'];g=s['group']
        if not g:
            g=net.create_network_security_group(oci.core.models.CreateNetworkSecurityGroupDetails(
                compartment_id=s['cid'],vcn_id=s['vcn'].id,display_name=s['name'],
                freeform_tags=dict(okd_cluster_uid=s['item']['uid'],submariner_peer_uid=s['peer_uid'])),opc_retry_token=secrets.token_hex(16)).data
        g=net.get_network_security_group(g.id).data;owned(g,s['cid'],s['vcn'].id,s['item']['uid'],s['peer_uid'])
        current=oci.util.to_dict(allr(net.list_network_security_group_security_rules,g.id).data)
        missing=[r for r in s['rules'] if not any(matching(a,r) for a in current)]
        def model(r):
            r=dict(r)
            if 'udp_options' in r:r['udp_options']=oci.core.models.UdpOptions(destination_port_range=oci.core.models.PortRange(min=4500,max=4500))
            if 'icmp_options' in r:r['icmp_options']=oci.core.models.IcmpOptions(type=8)
            return oci.core.models.AddSecurityRuleDetails(**r)
        if missing:net.add_network_security_group_security_rules(g.id,oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(security_rules=[model(r) for r in missing]))
        response=net.get_vnic(s['vnic'].id);v=response.data
        require(v.compartment_id==s['cid'] and v.subnet_id==s['subnet'].id and v.private_ip==s['address'] and not v.public_ip,'VNIC scope changed before attachment.')
        desired=list(v.nsg_ids or [])
        if g.id not in desired:
            require(len(desired)<5 and response.headers.get('etag'),'Cannot attach gateway NSG safely.')
            net.update_vnic(v.id,oci.core.models.UpdateVnicDetails(nsg_ids=desired+[g.id]),if_match=response.headers['etag'])
        actual=oci.util.to_dict(allr(net.list_network_security_group_security_rules,g.id).data)
        require(all(any(matching(a,r) for a in actual) for r in s['rules']) and g.id in (net.get_vnic(v.id).data.nsg_ids or []),'Gateway NSG readback differs.')
        receipt.append(dict(region=s['values']['OCI_REGION'],cluster_uid=s['item']['uid'],peer_uid=s['peer_uid'],gateway=s['item']['gateway'],address=s['address'],vnic_id=v.id,nsg_id=g.id,nsg_name=g.display_name))
    private_write(w.state/'gateway-network.json',receipt)
    print('READY: dedicated gateway NSGs attached in the selected compartment. Shared routes/lists/IAM unchanged. Rule reads do not prove packet delivery or MTU; verify peer paths before joining.')


if __name__=='__main__':
    os.umask(0o077)
    try:main(sys.argv[1])
    except Exception as error:sys.exit('Gateway network preparation stopped: '+str(error))
