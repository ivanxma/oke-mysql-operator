"""Add a dedicated Geneve NSG to verified OKD VNICs; preserve shared rules."""
import json
import oci

OWNER = 'okd-mysql-oci-storage'


def prepare(inventory, compute, network, cluster_uid, state):
    cid, vcn_id = inventory['compartment_id'], inventory['vcn_id']
    if network.get_vcn(vcn_id).data.compartment_id != cid:
        raise ValueError('VCN is outside the authorized compartment; ask its owner to prepare overlay networking')
    planned = []
    for item in inventory['nodes']:
        instance = compute.get_instance(item['instance_id']).data
        if instance.compartment_id != cid or instance.lifecycle_state != 'RUNNING':
            raise ValueError('Instance scope/state changed')
        ip = next(a['address'] for a in item['node']['status']['addresses'] if a['type'] == 'InternalIP')
        attachments = oci.pagination.list_call_get_all_results(compute.list_vnic_attachments,
                        compartment_id=cid, instance_id=item['instance_id']).data
        matching = []
        for attachment in attachments:
            if attachment.compartment_id != cid or attachment.lifecycle_state != 'ATTACHED':
                continue
            response = network.get_vnic(attachment.vnic_id)
            if response.data.private_ip == ip:
                matching.append(response)
        if len(matching) != 1 or matching[0].data.compartment_id != cid:
            raise ValueError('Cannot uniquely verify the node VNIC in the authorized compartment')
        response = matching[0]
        subnet = network.get_subnet(response.data.subnet_id).data
        if subnet.vcn_id != vcn_id or subnet.compartment_id != cid:
            raise ValueError('Node subnet scope changed')
        planned.append(response)
    name = 'okd-mysql-overlay-'+cluster_uid[:8]
    existing = oci.pagination.list_call_get_all_results(network.list_network_security_groups,
                    compartment_id=cid, vcn_id=vcn_id, display_name=name).data
    if len(existing) > 1:
        raise ValueError('Dedicated overlay NSG selection is ambiguous')
    tags = {'managed-by': OWNER, 'cluster-uid': cluster_uid}
    if existing:
        group = network.get_network_security_group(existing[0].id).data
        if group.compartment_id != cid or group.vcn_id != vcn_id or group.freeform_tags != tags:
            raise ValueError('Refusing an unmanaged or incorrectly scoped overlay NSG')
    else:
        if any(len(r.data.nsg_ids or []) >= 5 for r in planned):
            raise ValueError('A target VNIC already has the maximum NSG count')
        group = network.create_network_security_group(oci.core.models.CreateNetworkSecurityGroupDetails(
                    compartment_id=cid, vcn_id=vcn_id, display_name=name, freeform_tags=tags)).data
    rules = oci.pagination.list_call_get_all_results(network.list_network_security_group_security_rules, group.id).data
    desired = [dict(direction='INGRESS', source=group.id, source_type='NETWORK_SECURITY_GROUP'),
               dict(direction='EGRESS', destination=group.id, destination_type='NETWORK_SECURITY_GROUP')]
    if rules:
        def valid(rule, direction):
            ref = rule.source if direction == 'INGRESS' else rule.destination
            ref_type = rule.source_type if direction == 'INGRESS' else rule.destination_type
            port = rule.udp_options.destination_port_range if rule.udp_options else None
            return (rule.direction == direction and rule.protocol == '17' and ref == group.id and
                    ref_type == 'NETWORK_SECURITY_GROUP' and not rule.is_stateless and port and
                    port.min == 6081 and port.max == 6081 and not rule.udp_options.source_port_range)
        if len(rules) != 2 or not all(any(valid(r, d['direction']) for r in rules) for d in desired):
            raise ValueError('Dedicated NSG has unexpected rules; review explicitly')
    else:
        network.add_network_security_group_security_rules(group.id,
            oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(security_rules=[
                oci.core.models.AddSecurityRuleDetails(protocol='17', is_stateless=False,
                    udp_options=oci.core.models.UdpOptions(destination_port_range=oci.core.models.PortRange(min=6081, max=6081)),
                    description='OKD Geneve between verified cluster VNICs only', **d) for d in desired]))
    for response in planned:
        # Refetch immediately before updating and preserve every existing NSG.
        latest = network.get_vnic(response.data.id)
        if latest.data.compartment_id != cid or latest.data.subnet_id != response.data.subnet_id:
            raise ValueError('VNIC identity/scope changed before association')
        ids = list(latest.data.nsg_ids or [])
        if group.id not in ids:
            if len(ids) >= 5:
                raise ValueError('VNIC NSG count changed; refusing association')
            network.update_vnic(latest.data.id, oci.core.models.UpdateVnicDetails(nsg_ids=ids+[group.id]),
                                if_match=latest.headers['etag'])
    path = state/'overlay-nsg.json'
    path.write_text(json.dumps({'nsg_id': group.id, 'compartment_id': cid,
                               'vnic_ids': [r.data.id for r in planned]}, indent=2)+'\n')
    path.chmod(0o600)
    print('Dedicated UDP 6081 overlay NSG attached to verified cluster VNICs; existing NSGs and shared security lists preserved.')
