# Configuration and private state

The sourceable examples are [community.env.example](../community.env.example)
and [community.optional.env.example](../community.optional.env.example). They
contain placeholders and generic defaults, not deployment inventory.

The step-00 generator creates a mode-600 mandatory profile and optional companion
under `internal_doc/regions/<region>/<deployment>/`. Existing files are retained.
The optional file loads first; explicit primary settings take precedence. The
low-level regional `00-init-config.sh` remains available for advanced orchestration.

## Generated execution folder (recommended)

On the OL9 jump host, after git clone, run from the repository root:

```bash
./openshift/community/00-create-workspace.sh --region uk-london-1
cd internal_doc/regions/uk-london-1/jumphost
./00-check-config.sh --preparation
./01-prepare-host.sh
```

The generator asks only for unknown inputs: existing SSH key path, controlled
DNS base domain, private node-subnet CIDR and authorized compartment name/ID.
It reads canonical region and host compartment ID from
[OCI IMDSv2](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/gettingmetadata.htm)
when available, without asking you to retype discovered IDs. If the deployment
compartment differs from the host compartment, explicitly supply the authorized
`--compartment-id` and `--compartment-name`. Discovery is not a permission grant.

Defaults include loopback SSH, the current account, a region-based cluster name,
planned pod/service CIDRs, and a separate administration media workspace. Review
network defaults against all connected networks. A new configuration is rejected
if its DNS identity or overlay ranges collide with saved deployment profiles.
No target IP is selected and the private key is never copied into the clone.

The generated folder contains:

```text
community.env                private configuration
community.optional.env       isolated .state selection
00-env.sh                    source only for direct oc commands
00-check-config.sh           offline checks
01-...sh through 12-...sh     launchers selecting this profile automatically
START-HERE.txt               ordered commands
```

All numbered installation commands run from this folder. The launchers resolve
the shared implementation relative to the clone; keep the folder inside that
clone. The separate `$ADMIN_WORKDIR` stores downloaded media/authentication and
is prepared by step 01. You do not need to create, download or upload profile
files manually. Existing protected configuration/state is reused. Generated
launchers with user edits are not overwritten. Rerun the generator to add missing
launchers after an update; do not repeat completed installation steps.

Use `--name <deployment>` for another folder in the same region. Additional
options are available via `00-create-workspace.sh --help`, including `--ssh-key`,
`--base-domain`, `--machine-cidr`, `--cluster-name`, `--pod-cidr`, `--service-cidr`
and `--admin-workdir`. `--non-interactive` fails on missing values instead of
prompting. Configuration files are mode 600; launchers and their folder are mode
700. Generated output remains under Git-ignored `internal_doc/regions/`.

The regional source wrappers and env examples below remain available for advanced
orchestration. The generated-folder workflow is the normal jump-host entry point.

## Required before image preparation

| Setting | Meaning |
| --- | --- |
| `ADMIN_HOST`, `ADMIN_USER` | Admin-host IPv4 address and SSH account; loopback can be used when running on that host. |
| `SSH_IDENTITY` | Existing protected private key on the invoking host. |
| `ADMIN_WORKDIR` | Dedicated absolute workspace under `/home/` on the admin host. Use separate, non-nested workspaces for clusters. |
| `TARGET_USER` | `core` for installed SCOS nodes. |
| `CLUSTER_NAME`, `BASE_DOMAIN` | Cluster DNS identity and controlled private base domain. |
| `MACHINE_CIDR` | Actual private node-subnet CIDR. |
| `POD_CIDR`, `SERVICE_CIDR` | Aligned, non-overlapping overlay networks; check against all connected networks and other clusters. |
| `INSTALL_DISK` | Device overwritten when the new VM boots its installer image. Verify against the intended OCI attachment. |
| `OCI_REGION` | Must match the selected regional folder. |
| `OCI_AUTH` | `instance_principal` only. |
| `OCI_COMPARTMENT_ID`, `OCI_COMPARTMENT_NAME` | Explicitly authorized deployment compartment. |

`NODE_IP` is not a preparation input. After manual VM creation, register the
actual assigned address with `05-register-node-ip.sh`. Never invent an address
merely to pass a configuration check. Worker IP lists are not required in advance.

The same source can run on a workstation or the admin host. Key paths and private
state are local to the invoking host; do not copy a workstation profile over a
working admin-host profile. Loopback SSH must work when using a loopback admin
address.

## Optional settings

| Setting | Default / purpose |
| --- | --- |
| `OKD_VERSION` | Pinned by the shared scripts; select a compatible public release deliberately. |
| `OCI_BUCKET`, `OCI_NAMESPACE` | Normally discovered/generated and registered by step 02. |
| `CLIENT_CIDR` | Optional evidence for client-facing security-rule auditing. Missing evidence produces a warning. |
| `EXPECTED_NODE_COUNT` | Initially 1; update after approved workers join. |
| `WORKER_INSTALL_DISK` | Worker installation device. |
| `INGRESS_CA_FILE` | CA bundle on the admin host when using a custom ingress certificate. |
| `KUBECONFIG` | Defaults to `$ADMIN_WORKDIR/sno/auth/kubeconfig`. |
| `OKD_STATE_DIR` | Defaults to ignored per-region local state. |
| `SOCKS_PORT` | Optional browser tunnel port. |

Source `openshift/community/regions/<region>/env.sh` from the repo root to select a site. The shared installation scripts
reuse that selection through `OKD_CONFIG_FILE`. Each region has independent
workspace, SSH state, bucket registration and kubeconfig. Regional checks
currently require the two configured sites to use the same authorized compartment;
use separate checkouts/configuration inventories for different tenancies.
An OCI region change alone does not change instance-principal tenancy identity.

## Generated state

| Location | Contents |
| --- | --- |
| `$OKD_STATE_DIR` | Protected local SSH configuration, known hosts, bucket/node registration and validation reports. |
| `$ADMIN_WORKDIR/downloads/<release>/` | Downloaded OKD tool archives, signature and checksums. |
| `$ADMIN_WORKDIR/scos-live.iso` | Verified base ISO. |
| `$ADMIN_WORKDIR/sno/` | Installation state, Ignition, logs and protected authentication files. Preserve it. |
| `$ADMIN_WORKDIR/okd-sno-oci.iso` | Generated control-plane installer media. |
| `$ADMIN_WORKDIR/oci-control-plane-image.json` | Image import receipt, scope, checksum and image availability. |
| `$ADMIN_WORKDIR/oci-dns-view.json` | Discovered subnet/VCN/resolver/view bound to this cluster and region. |
| `$ADMIN_WORKDIR/worker-image/` | Cluster-specific worker media and import receipt. |

`env.sh` loads generated bucket/node registration. Re-source it after those
steps to refresh an existing shell. DNS configuration reads saved discovery
automatically and revalidates the live mapping; no manual view-OCID input is needed.

Select kubeconfig through the shared environment and exported `KUBECONFIG`.
Scripts do not fall back to `~/.kube/config`. Keep actual profiles, OCIDs,
addresses, key paths, access records, passwords, kubeconfigs, receipts and logs
out of the public repository. Keep the private key in its protected key directory.

## Updating an existing checkout after the folder move

Public OKD regional wrappers now live under `openshift/community/regions/`.
The former root-level folder belonged to OKD; it was not an OKE installation
component. Private profiles and generated state remain in `internal_doc/regions/`.

From the repository root, with the existing profile already selected:

```bash
git pull --ff-only
source ./openshift/community/regions/uk-london-1/env.sh "$OKD_CONFIG_FILE"
```

In a fresh shell, explicitly supply the existing private profile path instead of
an unset `OKD_CONFIG_FILE`. If a private helper references the old root `regions/`
path, update its selector to the new public path, or source the regional env
script directly. Keep the same private profile, workspace, bucket and state.
Continue at the next unfinished step; no download, host preparation, image build
or VM creation needs repeating solely because the source folder moved.
