# Submariner between independent OKD clusters

Run this optional lab after OKD acceptance and worker provisioning. Start with
one cluster, then explicitly enable peer enrollment. The scripts retain
OVN-Kubernetes and do not alter OCI networking or replicate storage/databases.

Run on the Linux admin host. Follow the [technical workflow](docs/workflow.md)
for private configuration, network prerequisites and the exact command order.

| Step | Action |
| --- | --- |
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
