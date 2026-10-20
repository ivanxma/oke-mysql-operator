# Install MySQL cluster3 in OKE c2

These scripts configure access to a second OKE cluster, install its own MySQL
Operator, and add `mysql-tertiary/mycluster3` as a replica of the existing
ClusterSet in c1. Access uses **`~/.kube/config2`**, context **`c2`**.

## Prerequisites

- An existing **ACTIVE** OKE cluster with three eligible Ready workers, working
  storage, and enough resources for three MySQL members and two Routers.
- Authenticated OCI CLI with cluster-access permissions, `kubectl`, Python 3,
  and Helm on the administration host.
- The c1 ClusterSet already installed and healthy. Both OKE clusters need
  bidirectional pod routing, UDP/TCP 53, and TCP/3306 connectivity.
- Private API access from the administration host on TCP/6443. Review the c2
  OCID, region, and endpoint in `config.env`.
- Distinct database namespaces between clusters; the default lab uses
  `mysql`/`mysql-secondary` in c1 and `mysql-tertiary` in c2.

See [network prerequisites and commands](../docs/oke-networking.md) before
installing across separate VCNs or regions.

## Configure access

```bash
cd ~/mysqlk8s/oke2
cp -n config.env.example config.env
chmod 600 config.env
vi config.env

./01-list-oke-clusters.sh
./02-create-kubeconfig.sh
./03-check-kubernetes.sh
```

The default configuration uses a private endpoint. Step 02 creates the separate
`config2` file and preserves c1's default kubeconfig. Stop if any step fails or
workers are NotReady.

## Add cluster3 in OKE c2

```bash
cd ~/mysqlk8s/oke2
cp -n cluster3.env.example cluster3.env
chmod 600 cluster3.env
vi cluster3.env

./04-install-mysql-operator.sh
./05-configure-cross-cluster-dns.sh
./06-create-cluster3.sh
./07-verify-cluster3.sh
```

Defaults: MySQL/edition inherited from the live primary, three members, two
Routers, `oci-bv`, a 10Gi PVC request per member, and server IDs starting at 3000.
OCI provisioned 50Gi per member in this lab. Size volumes for the source data.
The starter scripts require the existing c1 source to use self-signed TLS.

| Step | Purpose |
| --- | --- |
| 04 | Install/reuse the pinned operator chart in c2. |
| 05 | Configure remote database namespace DNS forwarding in both clusters. |
| 06 | Check connectivity and submit native ClusterSet replica bootstrap. |
| 07 | Wait for ONLINE 3/3 and verify replica role, read-only state, and replication. |

Step 07 expects cluster3 to be a replica. For subsequent switchovers, diagnostics,
DNS refresh, and kubeconfig wrapper usage, see
[c2 technical details](../docs/oke2-technical-details.md) and
[ClusterSet operations](../docs/technical-details.md#operational-switchovers-and-replication-channels).
