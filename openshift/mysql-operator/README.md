# MySQL ClusterSet within one OKD cluster

Numbered scripts deploy the same Oracle MySQL Operator/ClusterSet model as the
OKE workflow, adapted for OpenShift security and worker-only placement.

Both database clusters run in separate namespaces of **one OKD cluster**.
For primary and replica databases in independent regional OKD clusters, use the
[regional OKD MySQL workflow](../community/mysql-operator/README.md).

| Component | Namespace | Configuration |
| --- | --- | --- |
| Operator | `mysql-operator` | Official Helm chart 2.3.0; watches both database namespaces. |
| Primary InnoDB Cluster | `mysql` | `mycluster`: 3 MySQL members, 2 Routers; MySQL 26.7.0. |
| Replica InnoDB Cluster | `mysql-secondary` | `mycluster2`: 3 members, 2 Routers; native `initDB.clusterSet` bootstrap. |
| Selected workers | — | `worker01`, `worker02`, `worker03`, labelled `workload=mysql-cluster`. |

Short worker names resolve to a unique full registered node name. Labelling is
performed only when you execute **02-label-worker-nodes.sh**. It validates all
three nodes and server-side dry-runs every patch before changing any label.
It refuses missing/ambiguous names, control-plane nodes, unready/cordoned/tainted
workers and conflicting workload labels. It preserves a private before-label
journal. Each label change uses UID/resourceVersion tests against concurrent changes.

Both database clusters, their Routers and the operator require the workload label
and worker role. Required node affinity explicitly excludes the `control-plane`
and `master` roles, including a SNO node that also carries a worker role. Required
pod anti-affinity places each cluster's three database members on separate workers.
Each worker may host one primary-cluster and one replica-cluster member; the two
clusters share the same Kubernetes infrastructure and do not provide site-level DR.
The ClusterSet replication channel is asynchronous.

## Prerequisites

Run these scripts **on the OL9 administration/jump host**, with the full repository
checkout available. The path may be anywhere. Source that host's shared OKD
`env.sh` first to export `ADMIN_WORKDIR`, `KUBECONFIG` and the matching `oc`/`kubectl`
PATH. The scripts also source `openshift/community/env.sh`, which respects that
exported selection. They never select `~/.kube/config` or the OKE kubeconfigs.

Provide:

- Three Ready, uncordoned workers with no blocking scheduling taints. Assign the
  three intended names in private config if they differ from the defaults.
  Step 04 checks the CPU request budget against existing system pods. The lab
  may need a smaller explicit `MYSQL_SERVER_CPU_REQUEST`; this affects scheduling
  reservations and does not guarantee database performance.
- OCI instance-principal IAM for Block Volume creation/inspection/update/write,
  volume attachment creation/inspection/detachment, and instance attach/detach,
  scoped to the selected compartment. Existing boot-backup permissions alone do
  not grant `VOLUME_CREATE`. These scripts never grant IAM. A retained volume
  does not require automatic volume deletion permission.
- Cluster-admin access for the initial CRD, operator RBAC, dedicated namespaces
  and custom SecurityContextConstraints (SCCs). Step 03 uses OCI instance-principal
  authentication, verifies node instances in the privately selected compartment,
  and prepares Block Volume CSI. It does not change IAM, shared security lists or routing. The optional
  overlay flag adds the dedicated cluster NSG described below.
- A **working persistent StorageClass on OKD**, with supported CSI/local storage
  and correct topology, fsGroup and SELinux behavior. The SNO install does not
  install the OKE `oci-bv` provisioner. Step 03 prepares `mysql-oci-bv` when using OCI Block Volumes. Skip step 03
  only when a suitable storage provisioner is already installed. The CSI manifest
  is pinned and checksum-verified; the class uses `Retain`,
  `WaitForFirstConsumer` and paravirtualized attachments without changing the
  cluster default. Step 03 also applies Oracle’s pinned consistent-device-path
  MachineConfig to the worker pool and waits for it to finish. This can drain
  and reboot workers in turn. The step refuses a worker pool containing nodes
  outside the verified selection; it does not apply the upstream master config.
  The storage-node service account alone receives the dedicated
  host-access SCC needed by CSI; database accounts receive no such grant.
  OCI requires at least 50Gi per Block Volume, so request 50Gi for each database
  PVC when selecting this class (six volumes: at least 300Gi).
- The host-network CSI controller uses the node DNS resolver to reach OCI auth
  and regional API endpoints. Ensure the node resolver and outbound HTTPS work;
  a Ready container alone does not prove CSI connectivity. Step 03 waits for its
  CSI socket and a stable controller rollout.
- Six database PVCs, 50Gi requested each by default (300Gi total), plus
  application growth. Change the explicit class and size when using another backend.
  Each worker needs resources for two MySQL members (about 1.2 CPU / 2.25Gi total
  requested), plus Routers, the operator and existing workloads. Review actual
  allocatable capacity; a request is not a performance guarantee.
- OCI node-to-node UDP **6081** for OVN/Geneve. TCP rules alone do not
  permit cross-worker pod traffic. Step 03’s optional `--prepare-overlay` creates
  a tagged NSG with stateful UDP 6081 rules between its own members, then adds it
  to the verified cluster VNICs while preserving existing NSGs. It requires scoped
  NSG-create/rule/member, VCN-attach and VNIC-update/associate permissions. All
  referenced resources must be in the allowed compartment; shared security lists,
  routing and DRGs are not modified. NSG identity/tags and VNIC ETags are checked.
- DNS UDP/TCP 53; database cross-namespace TCP 3306, 33060 and 33061; Router access
  on service ports 3306/6447 and backend ports 6446–6450 as applicable; operator
  and sidecar access to the Kubernetes API (443/6443). Existing restrictive
  NetworkPolicies must permit these flows. Both namespaces use the same cluster
  DNS: **no cross-OKE CoreDNS forwarding or DRG change is needed**.
- Node HTTPS access to Oracle's container registry and admin access to the
  official MySQL Helm repository / Helm download endpoints.

The scripts do not install MySQL resources merely by being created or sourced.
Installation and validation are separate numbered steps; a successful script
creation or syntax check does not imply a successful live deployment.

## Configuration

From the repository root on the administration host:

```bash
# First source the existing administration environment using its actual path.
# ADMIN_WORKDIR and KUBECONFIG must already point to the intended OKD cluster.
source "$ADMIN_WORKDIR/env.sh"
mkdir -p internal_doc/openshift/mysql-operator
cp -n openshift/mysql-operator/config.env.example internal_doc/openshift/mysql-operator/config.env
chmod 600 internal_doc/openshift/mysql-operator/config.env
# Edit the private file: worker names, OCI compartment/region and STORAGE_CLASS.
# For step 03 use STORAGE_CLASS=mysql-oci-bv, REPLICA_STORAGE_CLASS=mysql-oci-bv,
# MYSQL_STORAGE_SIZE=50Gi and REPLICA_STORAGE_SIZE=50Gi.
source ./openshift/mysql-operator/env.sh
```

Use `MYSQL_OPENSHIFT_CONFIG` to select another private configuration file.
`MYSQL_EXPECTED_API_SERVER` derives from the existing
`$ADMIN_WORKDIR/cluster-domain`, or from the shared deployment environment.
You can explicitly set it privately. Every cluster command validates the selected
API before changing resources. Generated manifests, Helm values and label journals
stay under the private `MYSQL_STATE_DIR`, defaulting to
`internal_doc/openshift/mysql-operator/`; credentials are not placed in manifests.
The full checkout is needed because the adapters reuse the tested OKE helpers.
Before step 01, fill the private OCI fields if using step 03; this lets step 01
prepare the OCI SDK and YAML parser when they are absent. `MYSQL_OCI_PYTHON` may
select an existing administration Python environment.

## Installation

Run these individually and stop at the first error:

```bash
./openshift/mysql-operator/01-prepare-admin.sh
./openshift/mysql-operator/02-label-worker-nodes.sh
./openshift/mysql-operator/03-prepare-oci-storage.sh --prepare-overlay
# Omit --prepare-overlay if your network already permits node-to-node UDP 6081.
./openshift/mysql-operator/04-validate-environment.sh
./openshift/mysql-operator/05-install-mysql-operator.sh
./openshift/mysql-operator/06-create-primary-cluster.sh
./openshift/mysql-operator/07-verify-primary-cluster.sh
./openshift/mysql-operator/08-create-replica-cluster.sh
./openshift/mysql-operator/09-verify-clusterset.sh
# Optional functional write/read replication check:
./openshift/mysql-operator/10-test-replication.sh
```

| Script | Action / acceptance |
| --- | --- |
| 01 | Install admin prerequisites and pinned Helm; use the existing matching OKD CLI tools. |
| 02 | Label only the configured workers and classify them as `mysql-cluster`; exclude the control plane. |
| 03 | Prepare scoped OCI Block Volume CSI and `mysql-oci-bv`; verify instance-principal access and node compartment/topology before applying metadata or driver resources. Skip when using another existing provisioner. |
| 04 | Check API identity, labelled worker capacity, storage and administrative permissions. When database containers are running, also test member-to-member TCP/3306 through cluster DNS. |
| 05 | Create dedicated namespaces and narrowly scoped database SCC permissions; install the pinned official operator chart on workers. |
| 06 | Validate the primary manifest, create/reuse credentials and submit the first InnoDBCluster. Refuse orphan volumes/StatefulSets or conflicting configuration. |
| 07 | Check member TCP connectivity, then wait for primary members ONLINE, Router rollout and Ready pods; verify worker placement. |
| 08 | Bootstrap the replica in the second namespace through native ClusterSet support with separate server IDs. |
| 09 | Verify ClusterSet HEALTHY, replica read-only status and worker placement for both clusters; audit OCI volume compartments and worker attachments when both clusters use `mysql-oci-bv`. |
| 10 | Write a UUID audit row through the primary Router and wait for it on the replica; retain the row for audit. |
| 11 | Interactive MySQL Shell through the primary Router; `--replica` connects directly to replica member 0. |
| 12 | Optional loopback-only SQL port forward; `--replica` forwards a replica member. |

Step 06 prompts securely for a new root password. For unattended use, set
`MYSQL_ROOT_PASSWORD_FILE` to a protected single-line password file (mode 600).
An existing cluster's password is never regenerated. Replica credentials are
copied between Secrets through stdin/in-memory data and never printed. Existing
clusters are checked rather than silently converted, upgraded or rescaled.
Changing operator watch namespaces/topology after installation requires a separate
migration plan. These scripts do not automatically promote a replica or delete data.

## OpenShift security adaptation

Oracle's operator, database and Router images use fixed UIDs 2, 27 and 999,
respectively. OpenShift's default project UID ranges do not necessarily permit
these identities. Step 05 creates three custom SCCs with exactly those UIDs and
matching group ranges, namespace SELinux confinement, all capabilities dropped,
no privilege escalation, no host networking/hostPath, and RuntimeDefault seccomp.
Namespace Roles grant only `use` of the exact SCC to the operator or
cluster-specific sidecar/switchover service accounts. The namespace `default`
service accounts and all-service-account groups receive no grant. The built-in
`anyuid` and `privileged` SCCs are not modified or assigned.

The generated CR uses `datadirPermissions.setRightsUsingInitContainer=false` and
`fsGroupChangePolicy=OnRootMismatch` to avoid the default root `chown` init
container. The selected storage must actually allow the resulting UID/GID 27
filesystem access and correct SELinux labels. If it does not, correct the storage
configuration; do not switch to privileged containers or relabel host paths blindly.
The OCI Block CSI deployment has passed live validation: six Bound PVCs,
worker-only placement, both three-member clusters ONLINE, ClusterSet HEALTHY,
replica read-only status and a primary Router write observed on the replica.
Run steps 07–10 to verify each new environment.

## Operations and troubleshooting

```bash
./openshift/mysql-operator/11-connect-mysql.sh
./openshift/mysql-operator/11-connect-mysql.sh --replica
./openshift/mysql-operator/12-port-forward-mysql.sh
# In another shell on the admin host:
mysql -h 127.0.0.1 -P 3306 -u root -p
```

Try `SELECT @@hostname, @@version, @@read_only, @@super_read_only;`.
The replica inspection scripts bypass Router deliberately: a ClusterSet Router
may target the current global primary unless its routing options are explicitly
configured for local replica reads. Do not assume namespace-local Router placement
means namespace-local database traffic. Browse the OKD
console projects `mysql` / `mysql-secondary` for Pods, PVCs and Services. MySQL is
SQL/TCP; this workflow creates no HTTP Route or public load balancer. If forwarding
on the jump host, an additional workstation SSH tunnel is needed to reach its
loopback port; use the private deployment's existing SSH configuration.

| Symptom | Check |
| --- | --- |
| Wrong API / missing kubeconfig | Source the administration environment, verify exported KUBECONFIG exists on that host and correct the private expected API. |
| Missing workers or Pending pods | Inspect node names/Ready/taints and `workload=mysql-cluster`; confirm three eligible workers, anti-affinity and capacity. Control plane is deliberately excluded. |
| OCI CreateVolume NotAuthorizedOrNotFound | Have the compartment administrator add the scoped volume/attachment/instance permissions. The CSI controller retries existing PVCs. Step 07 fails clearly on recent Pending-claim authorization events and ignores stale pre-policy failures; do not recreate credentials or volumes. |
| No StorageClass / PVC Pending | Run step 03 for OCI storage or select an existing provisioner. Check CSI permissions, topology and storage events; OKE storage is not inherited. |
| MySQL containers run but member joins time out | Run step 04 to test member TCP/3306. Ensure node-to-node UDP 6081 is allowed for OVN/Geneve; TCP/3306 on the subnet alone does not enable overlay traffic. Use the dedicated NSG option after scoped IAM is granted, or have the network owner configure it. |
| CSI volume attached but mount waits for `/dev/oracleoci/...` | Run step 03 to install the pinned OCI worker device-naming MachineConfig; wait for the worker pool to finish updating. Do not substitute an unverified raw disk or format devices manually. |
| CSI controller restarts with OCI auth DNS timeout | Check the node resolver and outbound OCI API access. The scripted host-network controller uses `dnsPolicy: Default`; inspect its logs before changing IAM. |
| SCC rejection | Check generated service-account names, SCC-use RoleBindings and fixed UID; inspect namespace events. Do not grant broad anyuid/privileged access. |
| Permission denied on datadir | Confirm CSI fsGroup and SELinux handling for UID/GID 27, and root init is disabled. Do not reuse an old cluster's datadir. |
| ImagePullBackOff | Check node registry/DNS/HTTPS access and requested image/version availability. |
| Replica pod restarts during cloning | A MySQL server restart is expected during native CLONE recovery. Check clone progress and wait for ONLINE/readiness; a restart alone does not mean bootstrap failed. |
| Replica bootstrap stalled | Primary must be ONLINE/current ClusterSet primary; inspect cross-namespace DNS, TCP 3306, credentials, clone progress and operator logs. |
| Existing resource/configuration mismatch | Scripts refuse unsafe adoption, overwrite, bootstrap or upgrade. Review existing ownership/data before an explicit migration. |

Sources: [Oracle OCI OpenShift storage](https://github.com/oracle-quickstart/oci-openshift/blob/main/docs/STORAGE.md),
[OCI Block Volume provisioning and minimum size](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcreatingpersistentvolumeclaim_topic-Provisioning_PVCs_on_BV.htm),
[official chart](https://github.com/mysql/mysql-operator/tree/trunk/helm/mysql-operator),
[MySQL resource properties](https://dev.mysql.com/doc/mysql-operator/en/mysql-operator-properties.html),
[non-root data-directory permission option](https://dev.mysql.com/doc/relnotes/mysql-operator/en/news-9-1-0-2.2.2.html),
[ClusterSet documentation](https://dev.mysql.com/doc/mysql-shell/26.7/en/innodb-clusterset.html),
[OKD SCC administration](https://docs.okd.io/latest/authentication/managing-security-context-constraints.html).
