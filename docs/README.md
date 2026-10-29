# Documentation by Kubernetes platform

Choose [OCI OKE or OpenShift OKD](../README.md) before following installation
steps. The two workflows have separate configuration and execution folders.

## OCI OKE

| Guide | Contents |
| --- | --- |
| [OKE installation](oke-installation.md) | Prerequisites, root-level numbered scripts, first MySQL cluster and same-OKE ClusterSet replica. |
| [OKE MySQL technical details](technical-details.md) | Architecture, configuration, credentials, SQL access, replication, switchovers and troubleshooting. |
| [Second OKE cluster](../oke2/README.md) | Separate cluster access, local MySQL Operator and cross-OKE replica installation. |
| [OKE c2 technical details](oke2-technical-details.md) | Access isolation, wrappers, DNS, bootstrap behavior, reruns and operational scope. |
| [Connecting OKE clusters](oke-networking.md) | Shared VCN, same-region peering and cross-region DRG/RPC networking. |

## OpenShift OKD on OCI Compute

| Guide | Contents |
| --- | --- |
| [OKD installation](../openshift/community/README.md) | Jump-host clone, generated regional folder, custom-image import, node installation and workers. |
| [OKD prerequisites and implementation](../openshift/community/docs/installation.md) | Compute, storage, networking, DNS and IAM requirements. |
| [Regional OKD MySQL ClusterSet](../openshift/community/mysql-operator/README.md) | One Operator per OKD cluster; primary and replica in separate regional clusters. |
| [Submariner and Lighthouse](../openshift/community/networking/submariner/README.md) | Cross-cluster network connectivity and exported service DNS. |
| [▶ Watch the two-region MySQL demo](../openshift/community/demo/mysql-clusterset-two-regions.mp4) | MP4 walkthrough across London and Frankfurt OKD clusters. |
| [Restart health](../openshift/community/docs/restart-health.md) | Single-region and two-region restart checks, recovery and replication acceptance. |
| [OKD operations](../openshift/community/docs/operations.md) | Cluster validation, CLI/browser access, workers and troubleshooting. |
| [Same-cluster OKD MySQL](../openshift/mysql-operator/README.md) | Separate workflow for two database namespaces in one OKD cluster. |

## OKE architecture diagrams

Architecture sources and previews:

| Design | Source | Preview |
| --- | --- | --- |
| Same-region VCN peering | [draw.io](diagrams/network-same-region.drawio) | [PNG](diagrams/network-same-region.png) |
| Cross-region VCN peering | [draw.io](diagrams/network-cross-region.drawio) | [PNG](diagrams/network-cross-region.png) |
| Cross-OKE reference sample | [draw.io](diagrams/cross-oke-sample.drawio) | [PNG](diagrams/cross-oke-sample.png) |

Deployment-specific HTML runbooks, live inventory, and private configuration are kept
locally in the Git-ignored `internal_doc/` directory. They are not published here.
