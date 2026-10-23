# Submariner between independent OKD clusters

Run this optional lab after OKD acceptance and worker provisioning. Start with
one cluster, then explicitly enable peer enrollment. The scripts retain
OVN-Kubernetes and do not alter OCI networking or replicate storage/databases.

Run on the Linux admin host. Follow the [technical workflow](docs/workflow.md)
for private configuration, network prerequisites and the exact command order.

| Step | Action |
| --- | --- |
| 00 | Discover live settings and generate a private networking execution folder. |
| 01 | Download and verify the pinned `subctl` tool. |
| 02 | Validate cluster identities, versions, CIDRs and prerequisites. |
| 03 | Explicitly label the selected worker gateway. |
| 04 | Deploy the London broker. |
| 05 | Join London. |
| 06 | Inspect status. |
| 07 | Explicitly enroll the peer when approved. |
| 08 | Verify connectivity and discovery. |
| 09 | Gather private diagnostics. |

A single enrolled cluster cannot demonstrate a remote tunnel. Actual credentials,
addresses, inventories and diagnostic archives stay outside public source.

From the clone root after selecting the generated OKD environment:

```bash
source ./generated/openshift/uk-london-1/jumphost/00-env.sh
./openshift/community/networking/submariner/00-initialize.sh
cd generated/openshift/uk-london-1/jumphost/networking/submariner
./01-install-subctl.sh
```

Step 00 discovers CIDRs and eligible workers. A single control-plane node is not
used as the gateway by this workflow. Provision a cluster-matched SCOS worker
with OKD steps `09-build-worker-image.sh` and `10-import-worker-image.sh`, then
rerun initialization after it joins. After reviewing local network prerequisites, step 02 with
`--record-reviewed-prerequisites` checks versions/OVN/health and records the private
review settings. Peer gateway networking needs its own review before enrollment.
