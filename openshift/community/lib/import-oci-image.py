"""Import prepared OKD installation media; instance principals and explicit compartment only."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

import oci


def main():
    work, compartment_id, compartment_name, region, namespace, bucket_name, domain, version = sys.argv[1:9]
    role = sys.argv[9] if len(sys.argv) == 10 else 'worker'
    if role not in ('worker', 'control-plane'):
        raise RuntimeError('Expected worker or control-plane media')
    label = 'worker' if role == 'worker' else 'sno'
    os.umask(0o077)
    root = Path(work) / 'worker-image' if role == 'worker' else Path(work)
    media = root / ('okd-' + label + '-oci.iso')
    build = json.loads((root / ('build.json' if role == 'worker' else 'control-plane-build.json')).read_text())
    if (build['role'], build['architecture'], build['cluster_domain'], build['version']) != (
            role, 'x86_64', domain, version):
        raise RuntimeError('Image build does not match the configured cluster/release')
    digest = file_hash(media)
    if (root / (media.name + '.sha256')).read_text().split() != [digest, media.name]:
        raise RuntimeError('Installation media checksum mismatch')
    scope = dict(compartment_id=compartment_id, compartment_name=compartment_name,
                 region=region, namespace=namespace, bucket=bucket_name, sha256=digest)
    state_path = root / ('oci-worker-image.json' if role == 'worker' else 'oci-control-plane-image.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state and any(state.get(k) != v for k, v in scope.items()):
        raise RuntimeError('Saved import belongs to different media or OCI scope')
    state.update(scope)

    def save():
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(state_path)

    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    options = dict(signer=signer, timeout=(10, 60), retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    identity = oci.identity.IdentityClient(config, **options)
    storage = oci.object_storage.ObjectStorageClient(config, **options)
    compute = oci.core.ComputeClient(config, **options)
    compartment = identity.get_compartment(compartment_id).data
    if compartment.name != compartment_name or compartment.lifecycle_state != 'ACTIVE':
        raise RuntimeError('Configured compartment identity/state mismatch')

    def check_bucket():
        bucket = storage.get_bucket(namespace, bucket_name).data
        if bucket.compartment_id != compartment_id or bucket.public_access_type != 'NoPublicAccess':
            raise RuntimeError('Bucket must be private and inside the configured compartment')

    def checked_image():
        image = compute.get_image(state['image_id']).data
        if image.compartment_id != compartment_id:
            raise RuntimeError('Image is outside the configured compartment')
        return image

    check_bucket()
    if 'object' not in state:
        state.update(object='okd-' + label + '-' + secrets.token_hex(8) + '.iso',
                     import_token=secrets.token_hex(16), schema_token=secrets.token_hex(16))
        save()
    try:
        head = storage.head_object(namespace, bucket_name, state['object'])
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise
        print('Uploading verified installation media to private Object Storage', flush=True)
        oci.object_storage.UploadManager(storage, allow_parallel_uploads=True, parallel_process_count=3).upload_file(
            namespace, bucket_name, state['object'], str(media), if_none_match='*',
            metadata={'sha256': digest})
        head = storage.head_object(namespace, bucket_name, state['object'])
    headers = {key.lower(): value for key, value in head.headers.items()}
    if int(headers['content-length']) != media.stat().st_size:
        raise RuntimeError('Existing object size does not match this build')
    print('Verifying uploaded object SHA-256 before image import', flush=True)
    remote_digest = hashlib.sha256()
    response = storage.get_object(namespace, bucket_name, state['object'], if_match=headers['etag'])
    try:
        for chunk in response.data.raw.stream(8 * 1024 * 1024, decode_content=False):
            remote_digest.update(chunk)
    finally:
        response.data.close()
    if remote_digest.hexdigest() != digest:
        raise RuntimeError('Uploaded object SHA-256 does not match this build')
    if not state.get('image_id'):
        check_bucket()
        # Oracle documents importing installer ISO media with source type QCOW2.
        image = compute.create_image(oci.core.models.CreateImageDetails(
            compartment_id=compartment_id, display_name='okd-' + label + '-' + version + '-' + digest[:8],
            launch_mode='PARAVIRTUALIZED',
            image_source_details=oci.core.models.ImageSourceViaObjectStorageTupleDetails(
                namespace_name=namespace, bucket_name=bucket_name, object_name=state['object'],
                source_image_type='QCOW2', operating_system='Linux', operating_system_version='SCOS')),
            opc_retry_token=state['import_token']).data
        if image.compartment_id != compartment_id:
            raise RuntimeError('Created image has unexpected compartment')
        state['image_id'] = image.id
        save()
    deadline = time.monotonic() + 1800
    while True:
        image = checked_image()
        state['image_state'] = image.lifecycle_state
        save()
        if image.lifecycle_state == 'AVAILABLE':
            break
        if image.lifecycle_state != 'IMPORTING':
            raise RuntimeError('Image import ended in ' + image.lifecycle_state)
        if time.monotonic() >= deadline:
            raise TimeoutError('Import still running; rerun the import step to resume')
        print('Waiting for OCI image import', flush=True)
        time.sleep(30)

    if not state.get('schema_id'):
        checked_image()
        existing = oci.pagination.list_call_get_all_results(
            compute.list_compute_image_capability_schemas, compartment_id=compartment_id).data
        matches = [x for x in existing if x.image_id == image.id]
        if len(matches) > 1:
            raise RuntimeError('Ambiguous image capability schemas; review required')
        if matches:
            schema = compute.get_compute_image_capability_schema(matches[0].id).data
        else:
            globals_ = compute.list_compute_global_image_capability_schemas().data
            if len(globals_) != 1:
                raise RuntimeError('Ambiguous global image capability schema')

            def enum(value):
                return oci.core.models.EnumStringImageCapabilitySchemaDescriptor(
                    source='IMAGE', values=[value], default_value=value)

            data = {key: enum(value) for key, value in {
                'Compute.Firmware': 'UEFI_64', 'Compute.LaunchMode': 'PARAVIRTUALIZED',
                'Network.AttachmentType': 'PARAVIRTUALIZED',
                'Storage.BootVolumeType': 'PARAVIRTUALIZED',
                'Storage.RemoteDataVolumeType': 'PARAVIRTUALIZED'}.items()}
            data['Compute.SecureBoot'] = oci.core.models.BooleanImageCapabilitySchemaDescriptor(
                source='IMAGE', default_value=False)
            schema = compute.create_compute_image_capability_schema(
                oci.core.models.CreateComputeImageCapabilitySchemaDetails(
                    compartment_id=compartment_id, image_id=image.id,
                    compute_global_image_capability_schema_version_name=globals_[0].current_version_name,
                    display_name='okd-' + label + '-uefi', schema_data=data),
                opc_retry_token=state['schema_token']).data
        if schema.compartment_id != compartment_id or schema.image_id != image.id:
            raise RuntimeError('Capability schema is outside image/compartment scope')
        state['schema_id'] = schema.id
        save()
    schema = compute.get_compute_image_capability_schema(state['schema_id']).data
    if schema.compartment_id != compartment_id or schema.image_id != image.id:
        raise RuntimeError('Saved schema scope mismatch')
    for key, value in {'Compute.Firmware': 'UEFI_64', 'Compute.LaunchMode': 'PARAVIRTUALIZED',
                       'Network.AttachmentType': 'PARAVIRTUALIZED',
                       'Storage.BootVolumeType': 'PARAVIRTUALIZED'}.items():
        descriptor = schema.schema_data[key]
        if descriptor.values != [value] or descriptor.default_value != value:
            raise RuntimeError('Unexpected image capability: ' + key)
    if schema.schema_data['Compute.SecureBoot'].default_value is not False:
        raise RuntimeError('Secure Boot must be disabled for this lab image')
    shapes = oci.pagination.list_call_get_all_results(
        compute.list_image_shape_compatibility_entries, image.id).data
    state['compatible_shapes'] = [entry.shape for entry in shapes]
    state['uefi_configured'] = True
    save()
    print(role + ' custom image AVAILABLE, UEFI-only; no VM or pool created.')
    print('Private image ID and import state: ' + str(state_path))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    main()
