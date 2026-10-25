# MySQL Operator on OCI OKE and OpenShift OKD

This repository has **two Kubernetes deployment paths**. Choose your platform
first; each path has its own prerequisites, configuration and numbered scripts.

| Platform | Starting point | What the workflow does |
| --- | --- | --- |
| **OCI OKE** — OCI-managed Kubernetes | [OKE installation](docs/oke-installation.md) | Install MySQL Operator and MySQL InnoDB Cluster on an existing OKE cluster; optionally extend its ClusterSet to another OKE cluster. |
| **OpenShift OKD** — community OpenShift on OCI Compute | [OKD installation](openshift/community/README.md) | Prepare and install an OKD cluster on Compute VMs, add workers, then deploy MySQL and optional cross-region networking. |

## OCI OKE

Use the [OKE installation and prerequisites](docs/oke-installation.md).
Run its numbered scripts from the clone root with the OKE configuration selected.
The OKE cluster and worker infrastructure must already exist.

- [MySQL installation and same-OKE ClusterSet](docs/oke-installation.md)
- [Second OKE cluster](oke2/README.md)
- [OKE networking](docs/oke-networking.md)
- [MySQL operations and troubleshooting for OKE](docs/technical-details.md)

## OpenShift OKD on OCI Compute

Start with the [OKD installation guide](openshift/community/README.md).
Step 00 generates a private regional execution folder on the jump host; run the
remaining OKD steps there. This is the community OKD workflow.

After the OKD clusters are ready:

- [Regional MySQL ClusterSet: one MySQL Operator per OKD cluster](openshift/community/mysql-operator/README.md)
- [▶ Watch the two-region MySQL demo](https://ivanxma.github.io/oke-mysql-operator/) · [Demo notes](openshift/community/demo/README.md)
- [Submariner and Lighthouse: networking and DNS between independent OKD clusters](openshift/community/networking/submariner/README.md)
- [Health checks after restarting one or both regions](openshift/community/docs/restart-health.md)
- [OKD operations](openshift/community/docs/operations.md)

The separate [same-cluster OKD MySQL workflow](openshift/mysql-operator/README.md)
places primary and replica databases in different namespaces of one OKD cluster.
Use the regional workflow above for databases in two independent OKD clusters.

## Repository layout

| Location | Scope |
| --- | --- |
| Root numbered scripts and `config.env.example` | MySQL deployment on OKE. |
| `oke2/` | MySQL replica in a second OKE cluster. |
| `openshift/community/` | OKD installation, regional profiles, workers and operations. |
| `openshift/community/mysql-operator/` | MySQL ClusterSet across regional OKD clusters. |
| `openshift/community/networking/submariner/` | Cross-cluster connectivity and service discovery for OKD. |
| `openshift/mysql-operator/` | Two MySQL clusters within one OKD cluster. |

Defaults and configuration belong to the selected workflow. OKE's root
`config.env` and OKD's generated regional profiles are separate.
Keep real configuration, credentials and runtime state out of Git. New OKD
execution state is generated under ignored `generated/openshift/`; private
runbooks and deployment inventory remain under ignored `internal_doc/`.

See the [documentation index](docs/README.md) for all public guides.
