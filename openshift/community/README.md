# Community OKD on OCI Compute

This workflow prepares an **Oracle Linux 9 administration host** and installs
**SCOS on a new OCI Compute VM**. OL9 is not the cluster-node operating system.
The first cluster has one control-plane node running workloads; additional
workers are optional. No Red Hat account is required for this pinned public OKD
release. This is a community lab workflow, not a certified OCI OpenShift service.

Scripts resolve their supporting files relative to themselves. Keep deployment
addresses, keys, credentials, inventories and the internal installation guide in
the Git-ignored `internal_doc/` directory. The consolidated private guide is
`internal_doc/openshift/clean-installation.html`; it contains restricted access
information and must not be published.

## Prepare configuration

From the repository root (the checkout may live anywhere):

```bash
mkdir -p internal_doc/openshift
cp -n openshift/community/community.env.example internal_doc/openshift/community.env
chmod 600 internal_doc/openshift/community.env
source ./openshift/community/env.sh
```

Fill the private config with the admin host, local SSH key path, remote workspace,
planned node IP, subnet/DNS inputs, target OCI region, allowed compartment and
existing private bucket. Use `OKD_CONFIG_FILE` for another private config location.
Configure private DNS, routing, DHCP, registry egress and security rules before
building media. DNS must point to the planned private IP, which must be unused.
Use a new `ADMIN_WORKDIR` for a new cluster; retain existing installer state.

## Clean installation sequence

```bash
./openshift/community/01-prepare-host.sh
./openshift/community/00-validate-environment.sh
# Resolve FAILs; explicitly review WARN/ACTION items and planned network rules.
./openshift/community/02-download-okd.sh
./openshift/community/03-build-control-plane-image.sh
./openshift/community/04-upload-image.sh
# In OCI Console, launch ONE new VM from the resulting private custom image:
# configured compartment/region/subnet/IP, x86 shape, UEFI, Secure Boot disabled,
# paravirtualized network/boot volume. The guide lists the tested lab sizing.
# Do not rerun step 03 or delete installer state to monitor an active cluster.
./openshift/community/05-wait-for-control-plane.sh
./openshift/community/06-check-control-plane.sh
./openshift/community/07-deploy-demo.sh
./openshift/community/11-verify-web-access.sh --include-demo
# Optional browser access through the jump host:
./openshift/community/08-console-tunnel.sh
```

Step 04 now performs **upload, SHA-256 read-back, private image import and UEFI
capability verification**. It needs the OCI Python SDK (prepared by step 01),
instance-principal permissions and a private bucket in the configured compartment.
It creates no public URL/PAR, VM, IAM policy or network resource. Import state is
saved remotely in `$ADMIN_WORKDIR/oci-control-plane-image.json`; rerun step 04 to
resume. It will not import older media without its build metadata; preserve the
existing installed cluster and use a new workspace for a fresh installation.

Booting the imported installer image automatically overwrites `INSTALL_DISK`
inside the **newly launched VM**. Keep data volumes detached during installation.
The scripts do not terminate old VMs or replace their boot volumes. The installed
SCOS SSH user is `core`; update `TARGET_USER` in private config, then use
`01-prepare-host.sh --inspect-target` only if you need to refresh SSH state and
inventory after boot. A changed host key must be verified against the intended
instance before accepting it. The private SSH key stays on the invoking workstation.

| Step | Purpose / acceptance |
| --- | --- |
| 00 | Read-only planned-target preflight. `--existing-target` additionally audits an existing VM and its OCI networking; unavailable evidence remains unverified. Exit 0=checks pass, 1=failure, 2=warnings/unverified. |
| 01 | Prepare OL9 admin packages, public SSH key, OCI SDK and shared environment. Existing target inspection is optional. |
| 02 | Download pinned OKD tools; verify signing-key fingerprint, signature and SHA-256. |
| 03 | Check planned DNS and build matching SCOS SNO media with serial console and RAM boot. Refuses existing `sno/` state. |
| 04 | Import the control-plane custom image privately; verify compartment, object content, image availability and UEFI capabilities. |
| 05 | Wait for installer completion using the configured installation kubeconfig. Password stays in the protected auth file. |
| 06 | Check API identity, exact expected node count, Ready nodes, settled operators and updated machine-config pools. |
| 07 | Deploy the stateless hello demo using a pinned image digest. No PVC required. |
| 08 | Optional local SOCKS5 tunnel; configure browser DNS through the proxy. |
| 09 | Build cluster-specific worker media using the completed cluster's worker Ignition and matching SCOS release. |
| 10 | Import the worker custom image using the same private import checks as step 04. |
| 11 | Check console and optional demo HTTPS 200 with ingress CA verification from the admin host. |

`env.sh` is the only kubeconfig-selection entry point. Cluster commands require
its exported `KUBECONFIG` and verify the intended API; they never use
`~/.kube/config`. Paths such as `ADMIN_WORKDIR` and `KUBECONFIG` refer to the admin
host. Keep kubeadmin credentials and kubeconfig outside source control. For custom
ingress certificates, set `INGRESS_CA_FILE` to the CA bundle on the admin host.

All OCI writes use instance principals and verify resource compartment ownership.
The allowed compartment applies in every region. Denied access must be resolved
within that scope, never by switching to broader credentials. DNS changes are an
explicit OCI Console/network preparation step; review the private guide's exact
regional DNS requirements and IAM evidence. Release-specific bootstrap repair is
conditional troubleshooting, not an automatic change to every cluster.

## Optional workers

After the control plane completes installation:

```bash
./openshift/community/09-build-worker-image.sh
./openshift/community/10-import-worker-image.sh
```

Image details remain remotely in
`$ADMIN_WORKDIR/worker-image/oci-worker-image.json`. Step 09 reuses a verified
base ISO or downloads it with curl and preserves existing build directories.
Archive old worker-image state explicitly before rebuilding for a new release.
Step 10 can resume an import without creating another image.

Launch one new x86 worker VM from that image, verify the network and identity,
and review its client and serving CSRs individually before approval. Each worker
needs a unique hostname/IP and machine identity; never clone a joined node disk.
After workers join, set `EXPECTED_NODE_COUNT` to the total node count before step
06. An OCI instance configuration/pool may reference the installer image after a
single VM boot/join test. Pool scaling does not supply Machine API integration,
CSR approval, safe drain, or Kubernetes node deletion for `platform: none`.
Adding workers does not provide control-plane HA. Worker image import is verified;
worker VM boot/join and pool lifecycle remain separate acceptance tests.
