# Documentation

Installation and prerequisites: [main README](../README.md) and
[c2 installation](../oke2/README.md).

| Guide | Contents |
| --- | --- |
| [Technical details](technical-details.md) | Deployment architecture, ports, configuration, credentials, ClusterSet behavior, switchovers, replication channels, SQL access, and troubleshooting. |
| [OKE c2 technical details](oke2-technical-details.md) | Access isolation, wrappers, cluster3 DNS, bootstrap behavior, reruns, and operational scope. |
| [Connecting two OKE clusters](oke-networking.md) | Shared VCN, same-region peering, and cross-region DRG/RPC peering, with OCI diagrams and explicit commands. |

Architecture sources and previews:

| Design | Source | Preview |
| --- | --- | --- |
| Current MySQL deployment across c1 and c2 | [draw.io](diagrams/deployment.drawio) | [PNG](diagrams/deployment.png) |
| Same-region VCN peering | [draw.io](diagrams/network-same-region.drawio) | [PNG](diagrams/network-same-region.png) |
| Cross-region VCN peering | [draw.io](diagrams/network-cross-region.drawio) | [PNG](diagrams/network-cross-region.png) |
| Cross-OKE reference sample | [draw.io](diagrams/cross-oke-sample.drawio) | [PNG](diagrams/cross-oke-sample.png) |
