"""Verify scoped import and uploaded bytes before removing one local ISO."""
import hashlib
import json
from pathlib import Path
import shutil
import sys


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def cleanup(work, compartment_id, compartment_name, region, namespace, bucket, domain, version, role, action):
    import oci
    if role not in ('control-plane', 'worker') or action not in ('inspect', 'delete'):
        raise ValueError('Invalid cleanup role/action')
    root = Path(work)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('Expected an existing, absolute workspace directory')
    root = root / 'worker-image' if role == 'worker' else root
    if root.is_symlink():
        raise ValueError('Refusing symlink media directory')
    label = 'worker' if role == 'worker' else 'sno'
    media = root / ('okd-' + label + '-oci.iso')
    free = shutil.disk_usage(root).free / (1024 ** 3)
    print('Admin free disk: %.1f GiB' % free, flush=True)
    if media.is_symlink():
        raise ValueError('Refusing symlink ISO')
    if not media.exists():
        print('Generated ISO already absent; nothing deleted.')
        return
    build = json.loads((root / ('build.json' if role == 'worker' else 'control-plane-build.json')).read_text())
    if any(build.get(k) != v for k, v in dict(role=role, architecture='x86_64', cluster_domain=domain, version=version).items()):
        raise ValueError('Build does not match selected cluster/release')
    state = json.loads((root / ('oci-worker-image.json' if role == 'worker' else 'oci-control-plane-image.json')).read_text())
    scope = dict(compartment_id=compartment_id, compartment_name=compartment_name,
                 region=region, namespace=namespace, bucket=bucket)
    if any(state.get(k) != v for k, v in scope.items()):
        raise ValueError('Import receipt belongs to another OCI scope')
    digest = digest_file(media)
    if state.get('sha256') != digest or not state.get('uefi_configured'):
        raise ValueError('Missing successful import receipt or checksum mismatch')
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    options = dict(signer=signer, timeout=(10, 60), retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    identity = oci.identity.IdentityClient(config, **options)
    compartment = identity.get_compartment(compartment_id).data
    if compartment.id != compartment_id or compartment.name != compartment_name or compartment.lifecycle_state != 'ACTIVE':
        raise ValueError('Compartment identity/state mismatch')
    storage = oci.object_storage.ObjectStorageClient(config, **options)
    target_bucket = storage.get_bucket(namespace, bucket).data
    if target_bucket.compartment_id != compartment_id or target_bucket.public_access_type != 'NoPublicAccess':
        raise ValueError('Expected private bucket in selected compartment')
    image = oci.core.ComputeClient(config, **options).get_image(state['image_id']).data
    if image.compartment_id != compartment_id or image.lifecycle_state != 'AVAILABLE':
        raise ValueError('Custom image must be AVAILABLE in selected compartment')
    response = storage.get_object(namespace, bucket, state['object'])
    uploaded = hashlib.sha256()
    try:
        for chunk in response.data.raw.stream(8 * 1024 * 1024, decode_content=False):
            uploaded.update(chunk)
    finally:
        response.data.close()
    if uploaded.hexdigest() != digest:
        raise ValueError('Uploaded object checksum mismatch; retaining local ISO')
    print('Verified uploaded SHA-256 and AVAILABLE custom image.', flush=True)
    print('Cleanup candidate: %s (%.2f GiB)' % (media, media.stat().st_size / 1024 ** 3), flush=True)
    if action == 'delete':
        if media.is_symlink() or digest_file(media) != digest:
            raise ValueError('Local ISO changed during verification')
        media.unlink()
        print('Generated ISO deleted; base ISO, cloud object/image, credentials and receipts retained.')
        print('Admin free disk now: %.1f GiB' % (shutil.disk_usage(root).free / 1024 ** 3))


if __name__ == '__main__':
    cleanup(*sys.argv[1:])
