"""Create a cluster-owned node NSG without editing shared OCI networking."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def require(value, message):
    if not value:raise ValueError(message)


def owned(group, cid, vcn, uid):
    require(group.compartment_id==cid and group.vcn_id==vcn and
            (group.freeform_tags or {}).get('okd_cluster_uid')==uid and getattr(group,'lifecycle_state','AVAILABLE')=='AVAILABLE',
            'NSG ownership/compartment/VCN mismatch; no existing resource changed')


def rules(group):
    return [dict(direction='INGRESS',protocol='17',source_type='NETWORK_SECURITY_GROUP',source=group,
                 is_stateless=False,description='OKD node-to-node OVN Geneve'),
            dict(direction='EGRESS',protocol='17',destination_type='NETWORK_SECURITY_GROUP',destination=group,
                 is_stateless=False,description='OKD node-to-node OVN Geneve')]


def matches(rule, expected):
    if any(getattr(rule,k,None)!=v for k,v in expected.items() if k!='description'):return False
    options=rule.udp_options
    return bool(options and not options.source_port_range and options.destination_port_range and
                options.destination_port_range.min==6081 and options.destination_port_range.max==6081)


def memberships(vnic, cid, subnet, address, group):
    require(vnic.compartment_id==cid and vnic.subnet_id==subnet and vnic.private_ip==address and not vnic.public_ip,
            'Target VNIC identity/compartment/subnet differs; no attachment change')
    result=list(vnic.nsg_ids or [])
    if group not in result:result.append(group)
    require(len(result)<=5,'OCI VNIC NSG membership limit would be exceeded; preserve existing groups and review')
    return result


def write(path, value):
    require(not path.is_symlink(),'Refusing symlink state file')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp-'+secrets.token_hex(4))
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:out.write(json.dumps(value,indent=2)+'\n')
    temp.replace(path)


def main(args):
    work,state,selected,cid,cname,region,domain,address,machine,action=args
    require(action in ('preview','apply'),'Invalid action')
    root=Path(work);kube=Path(selected)
    require(kube.is_absolute() and kube.is_file() and kube.samefile(root/'sno/auth/kubeconfig'),'Use this installation shared KUBECONFIG')
    oc=[str(root/'bin/oc'),'--kubeconfig',str(kube),'--request-timeout=20s']
    def command(*args):return subprocess.check_output(oc+list(args),text=True,timeout=30).strip()
    require(command('config','view','--minify','-o','jsonpath={.clusters[0].cluster.server}')=='https://api.'+domain+':6443','Wrong cluster selected')
    uid=json.loads(command('get','namespace','kube-system','-o','json'))['metadata']['uid']
    nodes=json.loads(command('get','nodes','-o','json'))['items']
    require(len([n for n in nodes if any(a.get('type')=='InternalIP' and a.get('address')==address for a in n['status'].get('addresses',[]))])==1,
            'Registered target IP does not identify exactly one cluster node')
    import oci
    options=dict(signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    config={'region':region};network=oci.core.VirtualNetworkClient(config,**options)
    comp=oci.identity.IdentityClient(config,**options).get_compartment(cid).data
    require(comp.name==cname and comp.lifecycle_state=='ACTIVE','Compartment identity/state mismatch')
    candidates=[]
    for subnet in oci.pagination.list_call_get_all_results(network.list_subnets,cid).data:
        if subnet.compartment_id!=cid or subnet.cidr_block!=machine:continue
        for ip in oci.pagination.list_call_get_all_results(network.list_private_ips,subnet_id=subnet.id,ip_address=address).data:
            candidates.append((subnet,network.get_vnic(ip.vnic_id)))
    require(len(candidates)==1,'Target IP/subnet assignment is ambiguous or absent')
    subnet,response=candidates[0];vnic=response.data
    memberships(vnic,cid,subnet.id,address,next(iter(vnic.nsg_ids or []),'preview-only'))
    vcn=network.get_vcn(subnet.vcn_id).data
    require(vcn.compartment_id==cid,'VCN is outside the selected compartment')
    name='okd-nodes-'+uid[:8]
    groups=[g for g in oci.pagination.list_call_get_all_results(network.list_network_security_groups,cid,vcn_id=vcn.id).data
            if g.display_name==name]
    require(len(groups)<=1,'Ambiguous node NSG; preserve state for review')
    group=groups[0] if groups else None
    if group:owned(group,cid,vcn.id,uid)
    memberships(vnic,cid,subnet.id,address,group.id if group else 'preview-only')
    print('Plan: dedicated '+name+' NSG; stateful UDP 6081 only between its member VNICs; preserve existing memberships.',flush=True)
    if action=='preview':
        print('Read-only preview. Run with --apply to prepare the node NSG.');return
    if not group:
        group=network.create_network_security_group(oci.core.models.CreateNetworkSecurityGroupDetails(
            compartment_id=cid,vcn_id=vcn.id,display_name=name,freeform_tags={'okd_cluster_uid':uid}),opc_retry_token=secrets.token_hex(16)).data
    group=network.get_network_security_group(group.id).data;owned(group,cid,vcn.id,uid)
    existing=oci.pagination.list_call_get_all_results(network.list_network_security_group_security_rules,group.id).data
    missing=[r for r in rules(group.id) if not any(matches(e,r) for e in existing)]
    if missing:
        network.add_network_security_group_security_rules(group.id,oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
            security_rules=[oci.core.models.AddSecurityRuleDetails(**r,udp_options=oci.core.models.UdpOptions(
                destination_port_range=oci.core.models.PortRange(min=6081,max=6081))) for r in missing]))
    response=network.get_vnic(vnic.id);desired=memberships(response.data,cid,subnet.id,address,group.id)
    if desired!=list(response.data.nsg_ids or []):
        etag=response.headers.get('etag');require(etag,'No VNIC ETag; refusing unguarded membership update')
        network.update_vnic(vnic.id,oci.core.models.UpdateVnicDetails(nsg_ids=desired),if_match=etag)
    after=network.get_vnic(vnic.id).data
    require(group.id in memberships(after,cid,subnet.id,address,group.id) and group.id in (after.nsg_ids or []),'NSG attachment readback differs')
    actual=oci.pagination.list_call_get_all_results(network.list_network_security_group_security_rules,group.id).data
    require(all(any(matches(e,r) for e in actual) for r in rules(group.id)),'NSG rule readback differs')
    receipt=dict(compartment_id=cid,region=region,cluster_uid=uid,cluster_domain=domain,nsg_id=group.id,nsg_name=name,
                 subnet_id=subnet.id,vcn_id=vcn.id,control_plane_vnic_id=vnic.id,port=6081,protocol='UDP')
    write(Path(state)/'worker-network.json',receipt)
    print('READY: control-plane attached. Select NSG '+name+' for each new worker. Private receipt: '+str(Path(state)/'worker-network.json'))


if __name__=='__main__':
    os.umask(0o077)
    try:main(sys.argv[1:])
    except Exception as error:sys.exit('Worker network preparation failed: '+str(error))
