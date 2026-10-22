#!/usr/bin/env python3
"""Prepare private image staging and generate scoped environment registration."""
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys


def write_private(path, text):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(4))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def environment(record):
    # This file is sourced only by env.sh. Guards run before any export.
    lines = ['# Generated private bucket registration; do not publish.']
    for key, field in [('OCI_REGION', 'region'), ('OCI_COMPARTMENT_ID', 'compartment_id')]:
        lines.extend([
            'if [[ ${' + key + ':-} != ' + shlex.quote(record[field]) + ' ]]; then',
            "  printf 'Bucket registration does not match selected " + key + ".\\n' >&2",
            '  return 1 2>/dev/null || exit 1',
            'fi'])
    for key, field in [('OCI_BUCKET', 'bucket'), ('OCI_NAMESPACE', 'namespace')]:
        lines.extend([
            'if [[ -n ${' + key + ':-} && ${' + key + '} != ' + shlex.quote(record[field]) + ' ]]; then',
            "  printf 'Configured " + key + " conflicts with the registered bucket.\\n' >&2",
            '  return 1 2>/dev/null || exit 1',
            'fi'])
    lines.extend('export ' + key + '=' + shlex.quote(record[field])
                 for key, field in [('OCI_BUCKET', 'bucket'), ('OCI_NAMESPACE', 'namespace')])
    return '\n'.join(lines) + '\n'


def validate_scope(record, compartment_id, compartment_name, region, namespace='', bucket=''):
    for field, expected in [('compartment_id', compartment_id), ('compartment_name', compartment_name), ('region', region)]:
        if record.get(field) != expected:
            raise ValueError('Saved bucket belongs to a different ' + field)
    for field, expected in [('namespace', namespace), ('bucket', bucket)]:
        if expected and record.get(field) != expected:
            raise ValueError('Configured ' + field + ' differs from saved bucket registration')


def provision(work, compartment_id, compartment_name, region, namespace, bucket, cluster):
    import oci
    root = Path(work)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('Run step 01 first to prepare the absolute administration workspace')
    if not compartment_id.startswith('ocid1.compartment.') or not compartment_name or not region:
        raise ValueError('Select the authorized compartment ID/name and OCI region')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', cluster):
        raise ValueError('Set a valid cluster name before preparing its bucket')
    path = root/'oci-bucket.json'
    saved = json.loads(path.read_text()) if path.exists() else None
    if saved:
        validate_scope(saved, compartment_id, compartment_name, region, namespace, bucket)
        if saved.get('admin_workdir') != str(root) or saved.get('cluster') != cluster:
            raise ValueError('Saved bucket belongs to a different cluster/workspace')
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    options = dict(signer=signer, timeout=(10, 60), retry_strategy=oci.retry.NoneRetryStrategy())
    config = {'region': region}
    identity = oci.identity.IdentityClient(config, **options)
    compartment = identity.get_compartment(compartment_id).data
    if compartment.id != compartment_id or compartment.name != compartment_name or compartment.lifecycle_state != 'ACTIVE':
        raise ValueError('Authorized compartment identity/state mismatch')
    storage = oci.object_storage.ObjectStorageClient(config, **options)
    actual_namespace = storage.get_namespace().data
    if namespace and actual_namespace != namespace:
        raise ValueError('OCI_NAMESPACE does not match the instance principal tenancy')
    namespace = actual_namespace
    if saved:
        validate_scope(saved, compartment_id, compartment_name, region, namespace, bucket)
    bucket = bucket or (saved['bucket'] if saved else cluster + '-install-' + secrets.token_hex(4))
    if not bucket or any(c.isspace() for c in bucket):
        raise ValueError('Invalid bucket name')
    record = dict(compartment_id=compartment_id, compartment_name=compartment_name,
                  region=region, namespace=namespace, bucket=bucket, phase='planned',
                  admin_workdir=str(root), cluster=cluster)
    # Retain the chosen name before creating so interruption does not duplicate it.
    write_private(path, json.dumps(record, indent=2)+'\n')
    try:
        result = storage.get_bucket(namespace, bucket).data
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise
        try:
            storage.create_bucket(namespace, oci.object_storage.models.CreateBucketDetails(
                compartment_id=compartment_id, name=bucket, public_access_type='NoPublicAccess',
                storage_tier='Standard', freeform_tags={'managed-by': 'okd-community-install'}))
        except oci.exceptions.ServiceError as creation_error:
            # A concurrent creator is acceptable only after the same full checks.
            if creation_error.status != 409:
                raise
        result = storage.get_bucket(namespace, bucket).data
    if (result.compartment_id != compartment_id or result.name != bucket or
            result.public_access_type != 'NoPublicAccess' or result.storage_tier != 'Standard'):
        raise ValueError('Bucket must be private, Standard tier and inside the authorized compartment')
    record['phase'] = 'verified'
    write_private(path, json.dumps(record, indent=2)+'\n')
    write_private(root/'oci-bucket.env', environment(record))
    return record


def register(record, state, compartment_id, compartment_name, region, namespace, bucket, work=None):
    validate_scope(record, compartment_id, compartment_name, region, namespace, bucket)
    if work is not None and record.get('admin_workdir') != work:
        raise ValueError('Remote bucket registration belongs to a different workspace')
    if record.get('phase') != 'verified' or not record.get('bucket') or not record.get('namespace'):
        raise ValueError('Cannot register an unverified bucket')
    root = Path(state)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root/'bucket.json'
    if path.exists():
        validate_scope(json.loads(path.read_text()), compartment_id, compartment_name, region,
                       record['namespace'], record['bucket'])
    write_private(path, json.dumps(record, indent=2)+'\n')
    write_private(root/'bucket.env', environment(record))


def main():
    if len(sys.argv) == 10 and sys.argv[1] == '--register':
        # Mode used only locally; reads verified remote receipt from a private file.
        _, _, receipt, state, cid, name, region, namespace, bucket, work = sys.argv
        register(json.loads(Path(receipt).read_text()), state, cid, name, region, namespace, bucket, work)
        print('Bucket registered in the selected private environment. Source this region\x27s env.sh to refresh the current shell.')
    elif len(sys.argv) == 8:
        print(json.dumps(provision(*sys.argv[1:])))
    else:
        raise ValueError('Invalid bucket preparation arguments')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not dump SDK requests, credentials or resource identifiers to stdout.
        code = getattr(error, 'code', None)
        if code:
            sys.exit('Bucket preparation denied/failed (' + str(code) + '); check scoped bucket permissions. No alternate credentials were used.')
        sys.exit('Bucket preparation failed: ' + str(error))
