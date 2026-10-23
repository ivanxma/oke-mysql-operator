# Regional MySQL deployment details

Step 00 discovers both existing generated OKD profiles, API identities, cluster
UIDs and exact Ready worker names. It creates
`generated/openshift/<region>/<deployment>/mysql-operator/`, private configuration,
launchers, `.credentials/root-password` (mode 600), and `.state/`. Existing
passwords are checked, never rotated implicitly. `--deployment` selects a generated
name other than the default `jumphost`. `--lab` explicitly permits a single member
and an eight-character minimum password; no password is embedded in public source.

London uses namespace `mysql-cluster1` and `cluster1`; Frankfurt uses
`mysql-cluster2` and `cluster2`. Each cluster runs its own scoped official Oracle
MySQL Operator. The selected official chart is 2.3.1 (SHA-256 verified) and the
server/Router release is 26.10.0. This pins the release rather than using a floating
latest tag. The previous chart 2.3.0 archive is unavailable in the current official
Helm repository; the legacy same-cluster workflow is not invoked for chart install.

## Storage — step 03

`03-install-storage-class.sh` is a standalone per-region step. It validates the
explicit API/cluster UID, selected workers and private OCI compartment/region. A
matching `mysql-oci-bv` class is reused and CSI rollouts checked. Otherwise it uses
the existing tested OCI adapter with instance principals, scoped node inventory,
pinned/checksummed OCI manifests and worker-only consistent-device-path
MachineConfig. It preserves shared routing/security lists and the cluster default
StorageClass. The worker configuration can drain/reboot a gateway worker, briefly
interrupting the tunnel until it returns.

The class uses `blockvolume.csi.oraclecloud.com`, `Retain`,
`WaitForFirstConsumer`, ext4 and paravirtualized attachments. One 50Gi PVC per
region is requested for this lab. Driver SCC host access is limited to the CSI
node account; database/operator accounts use separate fixed-UID SCCs. No tenancy
IAM change, volume deletion or VM lifecycle action is scripted. If permission is
denied, have the compartment administrator review the reported operation; do not
switch credentials or recreate existing data.

## Cross-cluster DNS — steps 05/06

ServiceExport is prepared for each operator-managed headless instances service.
The immutable `serviceFqdnTemplate` includes the region's discovered Submariner
cluster ID before `{service}.{namespace}.svc.clusterset.local`; the operator
prefixes the pod name. MySQL's advertised addresses therefore resolve on both
clusters. This must be set before initial cluster creation, since local
`svc.cluster.local` addresses cannot identify remote membership reliably.

Frankfurt uses native `initDB.clusterSet` against the London member's exported
FQDN, with unique base server IDs (1000/2000), the same exact server version and
matching root credentials transferred only through protected file/Secret stdin.
The operator performs ClusterSet bootstrap/clone; the script refuses orphan
StatefulSets/PVCs, configuration mismatches and implicit upgrades or password changes.

## Security and acceptance

Operator, MySQL and Router retain fixed-UID, namespace-scoped SCC-use grants and
worker-only affinity excluding both control-plane roles. The one-member design
runs on the existing gateway worker and has no local database redundancy.
Self-signed MySQL TLS is used for this private test deployment; Kubernetes API
verification keeps the selected CA/credentials. No public MySQL Service or HTTP
Route is created.

Step 07 waits for ONLINE count and Router rollout and validates worker placement.
Step 08 uses authenticated MySQL Shell on both sites to require ClusterSet HEALTHY,
London PRIMARY, Frankfurt REPLICA and Frankfurt super_read_only. Step 09 writes a
UUID row on London and waits for it on Frankfurt, retaining it for audit.
Step 10 audits each actual PVC/PV/OCI volume and exclusive worker attachment in
the selected compartment and region, saving private evidence.
Replication is asynchronous; no failover/promotion is automatic. A replica Router
may route to the global primary according to ClusterSet routing policy; verification
connects directly to the Frankfurt member to establish replica read-only state.

Sources: [operator properties](https://dev.mysql.com/doc/mysql-operator/en/mysql-operator-properties.html),
[FQDN template behavior](https://dev.mysql.com/doc/relnotes/mysql-operator/en/news-8-4-0-2.1.3.html),
[official operator source](https://github.com/mysql/mysql-operator),
[official chart index](https://mysql.github.io/mysql-operator/index.yaml),
[OCI OpenShift storage](https://github.com/oracle-quickstart/oci-openshift/blob/main/docs/STORAGE.md).
