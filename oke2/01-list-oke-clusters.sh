#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
need oci
[[ "$OCI_COMPARTMENT_ID" == ocid1.compartment.* ]] || die 'Set OCI_COMPARTMENT_ID in oke2/config.env.'
oci2 ce cluster list --compartment-id "$OCI_COMPARTMENT_ID" --all \
    --query 'data[].{name:name,id:id,state:"lifecycle-state",vcn:"vcn-id"}' --output table
