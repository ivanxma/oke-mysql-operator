"""Guarded local-state cleanup for an explicitly terminated, replaced SNO VM.

Only cloud reads here; DNS changes use the existing scoped numbered script.
Installation credentials/media are retained because the replacement uses that image.
"""
import datetime
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def safe_path(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise ValueError('Expected an existing absolute workspace without symlink components')
    return path


def read(path):
    if path.is_symlink():
        raise ValueError('Refusing symlink state: ' + path.name)
    return json.loads(path.read_text())


def verify(work, state, cid, region, cluster, domain, machine, address, previous, compute, network, all_results):
    root, local = safe_path(work), safe_path(state)
    net = ipaddress.IPv4Network(machine)
    ip = ipaddress.IPv4Address(address)
    if ip not in net or ip in (net.network_address, net.network_address + 1, net.broadcast_address):
        raise ValueError('Replacement IP is not usable in the selected machine subnet')
    image = read(root / 'oci-control-plane-image.json')
    build = read(root / 'control-plane-build.json')
    if (image.get('compartment_id') != cid or image.get('region') != region or image.get('image_state') != 'AVAILABLE'
            or build.get('cluster_domain') != cluster + '.' + domain or build.get('machine_cidr') != machine):
        raise ValueError('Imported image/build does not match selected installation scope')
    old = compute.get_instance(previous).data
    if old.compartment_id != cid or old.lifecycle_state != 'TERMINATED' or old.image_id != image['image_id']:
        raise ValueError('Previous VM must be TERMINATED in the selected compartment and use this installation image')
    matches = []
    for subnet in all_results(network.list_subnets, cid).data:
        if subnet.compartment_id != cid or subnet.cidr_block != machine or subnet.lifecycle_state != 'AVAILABLE':
            continue
        for private in all_results(network.list_private_ips, subnet_id=subnet.id, ip_address=address).data:
            vnic = network.get_vnic(private.vnic_id).data
            if vnic.compartment_id != cid or vnic.subnet_id != subnet.id or vnic.public_ip:
                raise ValueError('Replacement VNIC scope/subnet/public IP mismatch')
            matches.append((subnet, vnic))
    if len(matches) != 1:
        raise ValueError('Need exactly one replacement IP assignment in the selected compartment/subnet')
    subnet, vnic = matches[0]
    attached = [a for a in all_results(compute.list_vnic_attachments, cid).data
                if a.vnic_id == vnic.id and a.lifecycle_state == 'ATTACHED']
    if len(attached) != 1:
        raise ValueError('Replacement must have exactly one attached instance')
    new = compute.get_instance(attached[0].instance_id).data
    if (new.id == old.id or new.compartment_id != cid or new.lifecycle_state != 'RUNNING'
            or new.image_id != image['image_id']):
        raise ValueError('Replacement instance/image/state/compartment mismatch')
    current = read(local / 'node-ip.json') if (local / 'node-ip.json').exists() else None
    scope = dict(region=region, compartment_id=cid, admin_workdir=work, cluster_name=cluster,
                 base_domain=domain, machine_cidr=machine)
    for path in (local / 'node-ip.json', root / 'oci-node-ip.json'):
        if path.exists():
            record = read(path)
            if any(record.get(k) != v for k, v in scope.items()):
                raise ValueError('Existing target registration belongs to a different installation')
            if current and record['node_ip'] != current['node_ip']:
                raise ValueError('Local/admin target registrations disagree; preserve state and review')
    saved = read(local / 'verified-target.json') if (local / 'verified-target.json').exists() else None
    if saved and saved.get('instance_id') not in (old.id, new.id):
        raise ValueError('Saved verified target belongs to another VM')
    if current and current['node_ip'] == address and (not saved or saved.get('instance_id') != new.id):
        raise ValueError('Already-registered replacement lacks matching verified VM state')
    if current and current['node_ip'] != address and saved and saved.get('instance_id') != old.id:
        raise ValueError('Stale registration is not bound to the terminated VM')
    if current and current['node_ip'] != address and not saved:
        raise ValueError('Need verified-target.json for old address ownership; preserve state and review')
    record = dict(instance_id=new.id, name=new.display_name, image_id=new.image_id,
                  private_ip=address, vnic_id=vnic.id, subnet_id=subnet.id, compartment_id=cid,
                  region=region, shape=new.shape, ocpus=new.shape_config.ocpus, memory_gbs=new.shape_config.memory_in_gbs)
    return record, current


def archive_state(work, state, current, record):
    root, local = safe_path(work), safe_path(state)
    if current and current['node_ip'] == record['private_ip']:
        print('Replacement already registered to verified VM; retaining current state (safe retry).')
        return
    history = local / 'history'
    if history.is_symlink():
        raise ValueError('Refusing symlink history directory')
    history.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination = history / ('replaced-' + stamp)
    candidates = [local / n for n in ('verified-target.json', 'node-ip.json', 'node-ip.env', 'installation-acceptance.json')]
    candidates += list(local.glob('validation-*.txt')) + list(local.glob('node-bootstrap-*.json'))
    candidates += [root / 'oci-node-ip.json', root / 'oci-node-ip.env', root / 'oci-dns-view.json', root / 'sno/.openshift_install.log']
    backups = [local / n for n in ('ssh_config', 'host.env', 'known_hosts')]
    if any(path.is_symlink() or path.resolve() != path for path in candidates + backups):
        raise ValueError('Refusing symlink target state; no files archived')
    destination.mkdir(mode=0o700)
    for path in backups:
        if path.exists():
            shutil.copy2(path, destination / path.name)
    for path in candidates:
        if path.exists():
            shutil.move(str(path), str(destination / path.name))
    if current and (local / 'known_hosts').exists():
        subprocess.run(['ssh-keygen', '-R', current['node_ip'], '-f', str(local / 'known_hosts')], check=True, stdout=subprocess.DEVNULL)
    path = local / 'verified-target.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(record, indent=2) + '\n')
    print('Archived stale target state:', destination)
    print('Preserved custom image, bucket, tools, installer metadata and matching auth files.')


def main(args):
    if len(args) != 10 or args[-1] not in ('preview', 'apply'):
        raise ValueError('Invalid replacement arguments')
    work, state, cid, region, cluster, domain, machine, address, previous, action = args
    import oci
    opts = dict(signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(), timeout=(10, 30),
                retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    record, current = verify(work, state, cid, region, cluster, domain, machine, address, previous,
                             oci.core.ComputeClient(config, **opts), oci.core.VirtualNetworkClient(config, **opts),
                             oci.pagination.list_call_get_all_results)
    print('PASS: old VM TERMINATED; replacement RUNNING, private, correct compartment/subnet/image.')
    print('Plan: archive stale target receipts; register replacement; revalidate DNS view; replace only the three cluster A sets; verify both hosts.')
    if action == 'apply':
        archive_state(work, state, current, record)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main(sys.argv[1:])
    except Exception as error:
        sys.exit('Replacement failed: ' + str(error))
