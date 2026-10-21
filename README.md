# MySQL InnoDB Cluster on Kubernetes — Linux 9

Numbered scripts install the official MySQL Operator and deploy MySQL InnoDB
Clusters on an existing Kubernetes environment. The optional `oke2/` workflow
adds a ClusterSet replica in a second OKE cluster.

Defaults: operator chart **2.3.0**, MySQL/Router **26.7.0**, Helm **v4.3.1**,
three MySQL members, two Routers, and a **10Gi PVC request per member**.

## Community OKD on OCI

For the separate SCOS single-node installation workflow, see
[Community OKD scripts and clean installation sequence](openshift/community/README.md).
Deployment-specific installation records and access details stay under the
Git-ignored `internal_doc/` directory.

For MySQL on that OKD cluster, use the numbered
[OpenShift MySQL Operator and ClusterSet scripts](openshift/mysql-operator/README.md).
They select labelled workers and keep database pods off the control plane.

## Prerequisites

- A Linux 9 administration host: Oracle Linux, RHEL, Rocky Linux, or AlmaLinux.
  Run as your normal user with `sudo` available for package/Helm installation.
- `kubectl` installed and connected to the intended cluster. Initial operator
  installation requires permissions for CRDs and cluster RBAC.
- Three Ready, uncordoned Linux amd64/arm64 workers without scheduling taints.
  Each MySQL pod requests 600m CPU and 1152Mi memory; allow additional capacity
  for init containers, Routers, the operator, and Kubernetes system pods.
- A working CSI provisioner and StorageClass. Set `STORAGE_CLASS` in `config.env`
  or provide one default class. OCI may provision **50Gi** for a 10Gi request;
  size storage for the database's data and growth.
- Working pod networking and DNS, including **UDP/TCP 53** between relevant
  workers/pods. Permit MySQL member traffic and retain the OKE baseline rules.
  See [network prerequisites and commands](docs/oke-networking.md).
- Worker HTTPS access to `container-registry.oracle.com/mysql`; administration
  host access to DNF repositories, `get.helm.sh`, and `mysql.github.io`.

Check the selected cluster before installing:

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl get storageclasses
```

## Install the first MySQL cluster

Run these commands from your repository checkout. Set `KUBE_CONTEXT` in
`config.env` to pin installation to the intended Kubernetes cluster.

```bash
cd ~/mysqlk8s
cp -n config.env.example config.env
chmod 600 config.env
vi config.env

./00-install-mysqlsh.sh       # Optional host MySQL Shell, latest Innovation channel
./01-install-prerequisites.sh
./02-install-helm.sh
./03-check-kubernetes.sh
./04-install-mysql-operator.sh
./05-create-mysql-cluster.sh
./06-verify-mysql-cluster.sh
```

Stop if a step fails. Step 05 prompts for a root password of at least 12
characters. Keep local configuration and password files out of source control.

| Step | Purpose |
| --- | --- |
| 00 | Install/update host MySQL Shell through the official EL9 Yum repository. |
| 01 | Install local utilities through DNF. |
| 02 | Install the pinned Helm binary after checksum verification. |
| 03 | Check Kubernetes readiness, placement, storage, and permissions. |
| 04 | Install the pinned MySQL Operator Helm chart and wait for readiness. |
| 05 | Create the namespace, root Secret, and MySQL cluster resource. |
| 06 | Wait for all configured MySQL members and Routers to be ready. |

Step 04 includes the Helm repository and chart commands. Step 05 creates the
database through the operator's `InnoDBCluster` resource. The default target is
`mysql/mycluster`.

For a one-worker lab, set `MYSQL_INSTANCES=1` and `MYSQL_ROUTER_INSTANCES=1`
before installation. This configuration cannot survive loss of that worker.

## Add mycluster2 in the same Kubernetes cluster

Complete the first installation, then run:

```bash
cd ~/mysqlk8s
cp -n clusterset.env.example clusterset.env
chmod 600 clusterset.env
vi clusterset.env

./11-check-clusterset.sh
./12-create-replica-cluster.sh
./13-verify-clusterset.sh
```

This adds `mysql-secondary/mycluster2` as a replica of the existing ClusterSet.
The existing operator handles both namespaces. Allow capacity for three more
MySQL members, two Routers, and three PVCs.

## Add mycluster3 in OKE c2

Use the [c2 installation guide](oke2/README.md). It configures
`~/.kube/config2`, installs c2's own MySQL Operator, sets up cross-cluster DNS,
and creates `mysql-tertiary/mycluster3` as a replica.

Both OKE clusters must have working private pod connectivity before creating
the replica. The [networking guide](docs/oke-networking.md) covers shared VCNs,
same-region peering, and cross-region peering.

## Documentation

| Guide | Contents |
| --- | --- |
| [Technical details](docs/technical-details.md) | Architecture, configuration, credentials, ClusterSet behavior, scripts 31–33, MySQL Shell, SQL access, port forwarding, and troubleshooting. |
| [OKE networking](docs/oke-networking.md) | OCI diagrams and commands for VCN/DRG/RPC routing, security rules, DNS, and connectivity checks. |
| [OKE c2 technical details](docs/oke2-technical-details.md) | Kubeconfig isolation, wrapper commands, cluster3 DNS, reruns, and operational limits. |
| [Documentation index](docs/README.md) | All guides and architecture diagrams. |

Based on the [official MySQL Operator](https://github.com/mysql/mysql-operator).
