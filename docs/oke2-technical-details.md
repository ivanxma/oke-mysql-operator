# OKE c2: technical details

Addresses and resource names in this public reference are illustrative; live inventory is kept in ignored `internal_doc/`.

Use the [c2 README](../oke2/README.md) for prerequisites and installation.
Run local wrapper commands below from `~/mysqlk8s/oke2`; root scripts 31–33
run from `~/mysqlk8s`.

Steps 01–03 configure access to an existing second OKE cluster. They create
**`~/.kube/config2`**, containing the single context **`c2`**. The first cluster's
`~/.kube/config` is preserved. They do not provision an OKE cluster or connect VCNs. Optional steps 04–07
install a separate MySQL Operator in c2 and create MySQL cluster3 as a replica
of the existing ClusterSet in c1.

The supplied configuration selects cluster `c2` in `eu-frankfurt-1`, in the same
VCN as the active `c1` observed during setup. Review its OCID in `config.env`.
Use an account with OCI permissions to access that cluster, configured OCI CLI
authentication, `kubectl`, and Python 3. Helm is needed only for `helm.sh`.

[Access setup commands](../oke2/README.md#configure-access)

Step 02 requires the OKE cluster to be `ACTIVE`; it stops without changing a
kubeconfig while the cluster is `CREATING`. Workers might still be provisioning
after the API becomes available, so check their readiness in step 03.

The default endpoint is `PRIVATE_ENDPOINT`. From the administration VM, routing
and NSG/security-list rules must allow its private API endpoint on TCP/6443.
Choose `PUBLIC_ENDPOINT` only if the cluster has a public endpoint and your
client is permitted to access it. Cluster access does not establish cross-cluster
pod routing or DNS.

Step 02 uses the official OCI CLI `create-kubeconfig --file` option, generates a
temporary isolated file, renames its context to `c2`, validates its OCI cluster
identity, and installs it with mode `600`. On reruns, an existing matching
config2 is backed up before refresh. A config2 pointing at another cluster, or
linked to the first cluster's config, is rejected. Authentication settings from
`OCI_PROFILE`/`OCI_AUTH` are retained in the generated exec configuration using
`--with-auth-context`. See the
[OCI CLI reference](https://docs.oracle.com/en-us/iaas/tools/oci-cli/latest/oci_cli_docs/cmdref/ce/cluster/create-kubeconfig.html).

Use the wrappers for subsequent c2 operations:

```bash
./kubectl.sh get nodes -o wide
./kubectl.sh get pods -A
./helm.sh list -A
```

The wrappers pass both the kubeconfig path and context explicitly, and export
`KUBECONFIG` only inside their own process. Running them does not change the
parent terminal's context or its default kubeconfig.

Equivalent direct commands are:

```bash
kubectl --kubeconfig "$HOME/.kube/config2" --context c2 get nodes
helm --kubeconfig "$HOME/.kube/config2" --kube-context c2 list -A
```

For a terminal dedicated to the second cluster:

```bash
export KUBECONFIG="$HOME/.kube/config2"
kubectl --context c2 get nodes
```

Use `unset KUBECONFIG` to return to the default `~/.kube/config`. Since `c2` is
stored in a separate file, plain `kubectl --context c2` cannot find it unless
you also select `config2` through `--kubeconfig` or `KUBECONFIG`.

`OKE2_CONFIG=/absolute/path/config.env` selects another settings file. Keep
local configuration and kubeconfig files out of source control.

## CNI and subnet capacity

Both OKE clusters share `10.60.10.0/24`. The subnet was expanded from `/25`
after c2 exhausted all 125 usable addresses during CNI initialization.
The UDP/53 security rule now also allows sources from `/24`. These are separate
settings: a DNS rule cannot create additional subnet IP addresses.

For NotReady nodes, inspect the actual network allocation state:

```bash
./kubectl.sh get nativepodnetworks
./kubectl.sh describe nodes
./kubectl.sh get events -A --field-selector type=Warning --sort-by=.lastTimestamp
./kubectl.sh -n kube-system get pods -o wide
```

`FailedToCreatePrivateIP` or `FailedToAttachVNIC` with an explicit subnet
exhaustion message identifies address capacity as the cause. All three c2 workers recovered automatically after expansion; their
NativePodNetworks reported `SUCCESS / COMPLETED`.
See the [deployment and cross-OKE reference diagrams](technical-details.md#deployment-architecture).

For VCN connectivity in the same region or across regions, use the
[OKE networking guide](oke-networking.md), including diagrams and the
OCI commands to build the private network path before deploying MySQL.

## Add cluster3 in OKE c2

The target is **`mysql-tertiary/mycluster3` in OKE c2**, using
`~/.kube/config2`, context `c2`. The existing `mysql/mycluster` and
`mysql-secondary/mycluster2` remain in c1. Cluster3 joins their existing
ClusterSet as a **REPLICA**; it does not create another independent ClusterSet.
Its source is the current ClusterSet primary, including after a switch between
mycluster and mycluster2.

Run these scripts from `/home/opc/mysqlk8s/oke2`:

[Cluster3 installation commands](../oke2/README.md#add-cluster3-in-oke-c2)

| Script | Action |
| --- | --- |
| `04-install-mysql-operator.sh` | Install pinned Helm chart 2.3.0 into c2's `mysql-operator` namespace, or reuse the matching deployed release. This is a separate operator installation from c1. |
| `05-configure-cross-cluster-dns.sh` | Configure namespace-specific DNS forwarding in **both** clusters; preserve other customizations and save ConfigMap backups under `../generated/`. |
| `06-create-cluster3.sh` | Check the existing ClusterSet and c2 operator, verify c2-to-c1 member DNS/TCP connectivity from a temporary pod, copy root credentials through memory/stdin, and submit native `initDB.clusterSet` bootstrap. |
| `07-verify-cluster3.sh` | Wait for MySQL and Routers; verify ClusterSet identity, healthy replica role, read-only state, reverse member DNS/TCP connectivity, and the active receiver's IO/SQL channel state. |

Defaults: three MySQL members, two Routers, `oci-bv`, **10Gi per PVC**, and
server-ID block **3000–3099**. The MySQL version and edition follow the live
primary. The existing c1 source must use self-signed TLS, as in this lab; managed
certificates need a separate TLS design. Allow enough volume capacity for the
source data. ClusterSet replication is asynchronous.

Preview the operator target and DNS changes before running them:

```bash
./04-install-mysql-operator.sh --dry-run
./05-configure-cross-cluster-dns.sh --dry-run
# After installing the operator and configuring DNS:
./06-create-cluster3.sh --dry-run
```

These previews make read-only API/MySQL status checks. The create preview does
not create the temporary network probe pod or prove end-to-end connectivity;
the actual create step performs that check before submitting the database.
The scripts use explicit kubeconfig files and contexts, and reject c1/c2 API
endpoints that point to the same Kubernetes cluster.

### DNS and network requirements

The observed c1 and c2 use VCN-native pod networking in the shared private
subnet **`10.60.10.0/24`**. Both use `cluster.local`, so forwarding that whole
zone would redirect local Kubernetes service lookups. Instead step 05 forwards:

- From c2 to c1: the namespaces containing c1 InnoDBClusters, currently
  `mysql.svc.cluster.local` and `mysql-secondary.svc.cluster.local`.
- From c1 to c2: `mysql-tertiary.svc.cluster.local`.

The namespace names must remain distinct across the two clusters. The script
rejects local services/namespaces that would be shadowed. The existing OKE
CoreDNS Corefile must import `custom/*.server`, mount `coredns-custom`, and
enable automatic reload. Only the `mysqlk8s-clusterset.server` key is managed.
It uses the documented [OKE custom DNS ConfigMap mechanism](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengconfiguringdnsserver.htm)
and [CoreDNS automatic reload](https://coredns.io/plugins/reload/), preserving
upstream pod IPs by avoiding a CoreDNS rollout. Allow a few minutes for
ConfigMap projection and reload.

By default, the forwarding targets are the current Ready CoreDNS **pod IPs**.
**Rerun step 05 when those IPs change**, including after CoreDNS rollouts or
worker replacement. For unattended operation, provide stable, reachable
resolver IPs in `C1_DNS_SERVERS` and `C2_DNS_SERVERS` using a separately managed
DNS endpoint design. This lab discovery step does not create stable DNS
endpoints or continuously reconcile them. Keep the DNS path working before
any maintenance or ClusterSet switchover.

Allow UDP/TCP 53 between the relevant DNS clients and CoreDNS pods, and TCP
3306 between all advertised MySQL members across both OKE clusters. Retain the
full OKE baseline worker/pod rules and intra-cluster Group Replication traffic.
Kubernetes NetworkPolicies must also permit these flows. ClusterIP addresses
are cluster-local; the scripts connect to individual member FQDNs backed by
VCN-routable pod IPs. No extra network plugin is installed for this same-VCN
setup. Different VCNs would additionally need routing/peering and non-overlapping
address space; these scripts do not build that connectivity.

### Reruns and operational scope

Step 06 reuses a matching, healthy existing cluster3 without changing it and
refuses conflicting Secrets, reused orphan PVCs/StatefulSets, or overlapping
server-ID blocks across the two Kubernetes clusters. An interrupted bootstrap
needs diagnosis rather than automatic deletion. Re-run step 07 to observe
progress; use c2 operator and sidecar logs if it does not become ONLINE:

```bash
./kubectl.sh -n mysql-operator logs deployment/mysql-operator --tail=100
./kubectl.sh -n mysql-tertiary get innodbclusters,pods,pvc,services -o wide
./kubectl.sh -n mysql-tertiary get events --sort-by=.lastTimestamp
./kubectl.sh -n mysql-tertiary logs mycluster3-0 -c sidecar --tail=100
```

Root script **31** cycles **mycluster → mycluster2 → mycluster3 → mycluster**
when run without an argument. Use `./31-toggle-clusterset-primary.sh 1`, `2`,
or `3` from the repository root to select a primary directly. Add `--dry-run`
to preview it. Selecting the current primary leaves the topology unchanged.
All ClusterSet members must be healthy. Step 31 can switch back from cluster3
through a c1 MySQL session, so the cross-OKE DNS and member connectivity must
remain available.

Script **32** still rotates the primary member only when the ClusterSet primary
is in the c1 pair; it refuses when cluster3 is primary. Step 07 verifies that
cluster3 is a **replica**, so it is not a primary-health check after promoting
cluster3. Inspect ClusterSet status through MySQL Shell for that case.

Root script **33** inspects the configured c1 pair; it does not scan c2. Use
step 07 for the three-cluster ClusterSet status and cluster3's active replication
receiver/source, including after a switch in c1.
