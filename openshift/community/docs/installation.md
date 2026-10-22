# Installation prerequisites and implementation

Follow the [short installation sequence](../README.md). Commands there run in
Bash from `openshift/community/`. Regional wrappers run the same scripts from
the repository root. Use either form consistently with the selected environment.

## Hosts and VM sizing

| Component | Requirements |
| --- | --- |
| Admin/jump host | Oracle Linux 9, x86_64, working SSH key and passwordless sudo, NTP, HTTPS access to public release/signing-key/container endpoints and OCI APIs. |
| Admin disk | At least 10 GiB free for validation; 30 GiB or more recommended for tools, ISOs and container storage. |
| Control-plane VM | New compatible x86 OCI VM running SCOS. A lab baseline is 4 OCPU / 8 vCPU, 48 GB memory and 150 GB boot storage. Validation minimums are 4 vCPU, nominal 16 GB memory and 120 GB disk; minimums do not guarantee capacity for added workloads. |
| Firmware/attachments | UEFI; Secure Boot disabled for this lab image; paravirtualized boot/network. Keep data volumes detached during disk installation. |
| Worker lab plan | Compatible x86 SCOS VM; 2 OCPU / 4 vCPU, 16 GB memory and at least 100 GB boot storage as a starting workload plan. |

Check the selected release, actual shape compatibility and workload sizing before
launch. OL9 is the administration OS; it is not installed in place as an OKD node.
The installed SCOS node account is `core`.

This is a community lab workflow, not a claim of certified OCI OpenShift support.
No script automatically launches, deletes or replaces a VM or its boot volume.

## Network preparation

Use a private subnet for cluster nodes. A public subnet can host the admin/jump
VM with scoped SSH access. Subnets in the same VCN use local routing. If hosts
are in different VCNs/regions, prepare the approved VCN/DRG routes and DNS
visibility before installation. An existing routed host path does not establish
pod/service connectivity between independent clusters.

| Traffic | Scope / purpose |
| --- | --- |
| TCP 22 | Approved client to jump host; jump host to node SSH. |
| TCP 6443 | Admin, nodes and approved client paths to control-plane API. |
| TCP 22623 | Cluster nodes to control-plane Machine Config Server. |
| TCP 10250 | Required control-plane/cluster components to node kubelets. |
| TCP 2379–2380 | Required control-plane etcd traffic only. |
| TCP 80/443 | Approved ingress clients and application paths. |
| UDP 6081 | OVN Geneve between cluster nodes when adding workers. |
| UDP/TCP 53 | Approved DNS resolvers; cluster DNS visibility. |
| UDP 123 | Approved time sources. |
| TCP 443 egress | Required registries, release downloads, repositories and OCI APIs. |
| Necessary ICMP | Path MTU discovery and approved health checks. |

Prepare stateful security-list/NSG rules with the actual permitted sources.
Private-node outbound access needs an available NAT/proxy/approved egress path;
a public jump host alone does not provide it. Preserve required baseline rules.
The validator audits selected effective OCI rules, routes and DHCP evidence; it
cannot prove every external firewall, client route, packet MTU or host policy.
Shared networking and IAM remain administrator-managed.

Machine, pod and service ranges must not overlap. For future Submariner use,
check all such ranges across both clusters. Pod/service CIDRs are virtual networks,
not one allocated OCI subnet address per pod/service.

See the [illustrative deployment diagram](architecture.md) for service placement.

## IAM and scope

All OCI changes use the admin instance principal in the selected region and
compartment. The scripts verify compartment ownership and stop on denial; they
never switch to a broader credential or alter tenancy IAM.

The admin principal needs these capabilities in that compartment:

| Area | Required capability |
| --- | --- |
| Identity | Read/verify selected compartment identity. |
| Network inspection | Read VCN, subnet, route/security rules, gateways and related node/VNIC inventory used by validation. |
| Media staging | Discover namespace; create/read a private Standard bucket; upload/read its objects. |
| Compute images | Create/read custom images and manage the image capability schema used to verify UEFI. |
| DNS discovery | Read private resolvers and views. |
| DNS creation/update | Create cluster zones and update their records. |

For DNS, this compartment-scoped template includes creation and record updates.
Replace the placeholders in the IAM Console, not in public script source:

```text
Allow dynamic-group <dynamic-group> to manage dns-zones in compartment <compartment>
Allow dynamic-group <dynamic-group> to use dns-records in compartment <compartment>
Allow dynamic-group <dynamic-group> to read dns-resolvers in compartment <compartment>
Allow dynamic-group <dynamic-group> to read dns-views in compartment <compartment>
```

`manage dns-zones` includes creation and deletion. `read` or `use dns-zones`
alone does not permit zone creation. Private zone creation also requires view
inspection, included by `read dns-views`. Record updates use both zone-update
and record-update permissions. Policies within a tenancy are not duplicated per
region unless they deliberately include regional restrictions. Another tenancy
requires its own authorized principal and compartment configuration.

## Media and first boot

1. Step 01 prepares admin tools, OCI SDK, public key and environment.
2. Step 02 creates/reuses a scoped private bucket and registers its settings.
3. Download scripts verify the OKD release signature/checksums and matching SCOS ISO.
4. Step 03 embeds this cluster's domain, network ranges, key and target disk.
5. Step 04 uploads media, verifies uploaded bytes, imports the custom image,
   waits for AVAILABLE and checks UEFI capabilities. It creates no public URL/PAR.
6. The operator manually creates a new VM from that image and registers its actual IP.
7. DNS discovery finds the configured subnet's VCN resolver default view. Preview
   reads only; explicit `--apply` creates/corrects only the cluster zone/records.
8. DNS verification polls both hosts; environment validation follows before final
   monitoring and acceptance.

Image preparation/import does not require a node IP. DNS is still required for
bootstrap. OCI normally starts a VM during creation, so prepare DNS promptly once
its actual address is known. If network/DNS preparation is not ready, stop only
the new VM while completing it; inspect its serial-console state before resuming.
A controlled first boot can use an explicitly selected launch address with DNS
prepared beforehand. Do not assume OCI assigned a planned address automatically.

The required records are `api.<cluster>.<domain>`, `api-int.<cluster>.<domain>` and
`*.apps.<cluster>.<domain>`, pointing to the registered private node IP in this
single-node direct-ingress lab. Both admin and node must resolve them. Do not
substitute a jump-host-only hosts entry for node DNS.

Preserve `sno/`, authentication and receipts. Re-running monitoring, DNS repair or
validation does not require rebuilding installer credentials. A build refuses
existing installation state; use a separate workspace for a new cluster.

## References

- [OKD single-node installation](https://docs.okd.io/latest/installing/installing_sno/install-sno-installing-sno.html)
- [OKD single-node prerequisites](https://docs.okd.io/latest/installing/installing_sno/install-sno-preparing-to-install-sno.html)
- [OCI installer-image upload/import and UEFI configuration](https://docs.oracle.com/en-us/iaas/Content/openshift-on-oci/installing-agent-image-creation.htm)
- [OCI DNS IAM permissions](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/dnspolicyreference.htm)
