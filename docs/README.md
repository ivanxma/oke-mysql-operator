# Documentation

Installation and prerequisites: [main README](../README.md) and
[c2 installation](../oke2/README.md).

| Guide | Contents |
| --- | --- |
| [Community OKD on OCI](../openshift/community/README.md) | Clean SNO installation sequence, private custom-image import, validation and optional worker preparation. |
| [Technical details](technical-details.md) | Deployment architecture, ports, configuration, credentials, ClusterSet behavior, switchovers, replication channels, SQL access, and troubleshooting. |
| [OKE c2 technical details](oke2-technical-details.md) | Access isolation, wrappers, cluster3 DNS, bootstrap behavior, reruns, and operational scope. |
| [Connecting two OKE clusters](oke-networking.md) | Shared VCN, same-region peering, and cross-region DRG/RPC peering, with OCI diagrams and explicit commands. |

Architecture sources and previews:

| Design | Source | Preview |
| --- | --- | --- |
| Same-region VCN peering | [draw.io](diagrams/network-same-region.drawio) | [PNG](diagrams/network-same-region.png) |
| Cross-region VCN peering | [draw.io](diagrams/network-cross-region.drawio) | [PNG](diagrams/network-cross-region.png) |
| Cross-OKE reference sample | [draw.io](diagrams/cross-oke-sample.drawio) | [PNG](diagrams/cross-oke-sample.png) |

Deployment-specific HTML runbooks, live inventory, and private configuration are kept
locally in the Git-ignored `internal_doc/` directory. They are not published here.
