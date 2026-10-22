# Two independent OKD clusters with Submariner — London-first lab

Run this workflow **after the London OKD installation has passed acceptance**.
It retains OVN-Kubernetes and adds Submariner connectivity/Lighthouse discovery.
It does not install OKE, replace the CNI, replicate storage/databases or automate
application failover. It makes no OCI networking or IAM changes.

The first phase changes **London only**: select a worker gateway, create the
broker in London and join London. A remote tunnel cannot exist with one member.
The second phase explicitly enrolls another independent cluster. That requires
changes to that peer as well; keep it disabled while testing London preparation.

## Prerequisites

- Run the scripts on the Linux administration/test VM where the protected
  London and peer installation kubeconfigs are accessible. These scripts run
  locally on that host; they do not use the Mac SSH orchestration wrappers.
- London installation steps 06 and 11 must pass first. All cluster nodes,
  operators and machine-config pools must be healthy and settled.
- Add **at least one Ready, schedulable SCOS worker in London**. This workflow
  deliberately requires a worker gateway; the initial single control-plane node
  alone is insufficient. One gateway per cluster is for testing, without HA.
- `oc`, Python 3 and HTTPS access to GitHub/Quay. Step 01 downloads pinned
  **subctl 0.24.2** from the official release repository and verifies its SHA-256.
  It installs only the client binary in a private state folder.
- Review the exact OKD/Kubernetes/Submariner release combination and the OVN
  Northbound database schema requirement (**6.1.0+**) before enabling installs.
  The script checks OVN configuration and requires that review to be recorded;
  it does not assert certification or discover every compatibility limitation.
- Non-overlapping machine, pod and service CIDRs within/between the two clusters.
  This workflow leaves **Globalnet disabled**. Each cluster keeps its own API,
  credentials, control plane, pods, service network and storage.
- Cluster-admin privileges for Submariner CRDs, RBAC, gateway DaemonSets and
  OpenShift SCC integration. `subctl` handles its OpenShift deployment security;
  do not grant broad workload SCC privileges manually.

### Network preparation — review before running

Have the networking owner prepare the approved DRG routes, private DNS and
scoped security rules. These scripts do not run `subctl cloud prepare` or alter
shared OCI NSGs/security lists.

| Path | Required traffic for this profile |
| --- | --- |
| Admin → each selected cluster API | TCP 6443; trusted API CA and DNS resolution |
| Participating cluster components → London broker API | TCP 6443; private DNS/routing and certificate validation |
| Gateway ↔ remote gateway | UDP 4500 and IPsec ESP protocol 50 for private, non-NAT connectivity |
| Within each OKD cluster | Preserve existing OVN Geneve UDP 6081 and all OKD baseline traffic |
| Hosts/pods → approved DNS/registries | UDP/TCP 53 and HTTPS egress as required |
| Network paths | Correct MTU and necessary ICMP discovery/health traffic |

The join uses `libreswan`, **`--natt=false`**, and broker certificate validation.
Use it only when gateway private IPs are directly routable through DRG paths.
The upstream NAT discovery port is UDP 4490 when using NAT traversal; this
first-cut profile does not configure NAT traversal. OVN-Kubernetes does not
require Submariner's generic intra-cluster UDP 4800 VXLAN path. Check current
upstream prerequisites before changing cable-driver/NAT options.

Existing OCI peering alone does not make pod/service networks reachable.
Submariner supplies the cross-cluster dataplane; do not blindly add all pod CIDRs
to OCI route tables. Validate actual packet paths, DNS and policy behavior.

## Configure privately on the administration host

Change to the synced source directory, then:

```bash
bash
mkdir -p internal_doc/regions/uk-london-1
cp -n openshift/community/networking/submariner/config.env.example \
  internal_doc/regions/uk-london-1/submariner.env
chmod 600 internal_doc/regions/uk-london-1/submariner.env
${EDITOR:-vi} internal_doc/regions/uk-london-1/submariner.env
```

A filled `internal_doc/regions/uk-london-1/community.env` must already exist in
this source checkout (the same regional inputs as the London installation).
The environment script selects the London profile and its exported KUBECONFIG;
it never uses `~/.kube/config`. Kubeconfig paths refer to files on this admin
host. Credentials are not copied to the Mac by these networking scripts.

Set the exact London gateway worker name and a unique cluster ID. After checking
acceptance, compatibility and networking, set the three corresponding review
flags to `true`. Keep `SUBMARINER_ENABLE_PEER=false`. Sensitive paths, node names,
endpoints and records stay in private configuration and generated state.

## Phase 1 — London only

```bash
./openshift/community/networking/submariner/01-install-subctl.sh
./openshift/community/networking/submariner/02-validate-environment.sh
./openshift/community/networking/submariner/03-label-london-gateway.sh
./openshift/community/networking/submariner/04-deploy-london-broker.sh
./openshift/community/networking/submariner/05-join-london.sh
./openshift/community/networking/submariner/06-show-status.sh
```

| Step | Behavior |
| --- | --- |
| 01 | Download/checksum/version-check pinned subctl; no Kubernetes changes. |
| 02 | Read cluster health, API identity, node/gateway eligibility and network ranges; save a private identity receipt. |
| 03 | Label only the selected London worker, with UID/resource-version guards. |
| 04 | Deploy broker CRDs/namespace in London; keep generated broker credentials private. |
| 05 | Install the Submariner operator/connectivity/discovery in London using the broker credentials. |
| 06 | Show status using saved identity; works even if cluster operators are no longer healthy. |

No peer kubeconfig is read and no peer cluster is changed in this phase. Expect
zero remote connections until a second member joins. Check the London
`submariner-operator` workloads and `subctl show all` output; status display alone
is not a cross-site acceptance test. Keep broker credentials and state intact.

The scripts refuse automatic CNI replacement, upgrades and reinstalling over an
existing Submariner namespace. If interrupted after resources are created, use
status/diagnostics and review actual state before resuming. Do not delete broker
credentials to force a rerun. Step 04 reuses an owned saved credential file.

## Phase 2 — enroll the peer only when ready

Full two-site connectivity requires Submariner on both clusters. Set these in
the private networking config only when the peer can be changed:

- `SUBMARINER_ENABLE_PEER=true` and `SUBMARINER_PEER_INSTALLATION_VERIFIED=true`.
- Peer kubeconfig, exact expected API, machine CIDR, unique ID and exact eligible
  worker gateway. Leave its context empty to select the current context in that
  explicit file, or pin the desired context name.

```bash
./openshift/community/networking/submariner/02-validate-environment.sh
./openshift/community/networking/submariner/07-join-peer.sh
./openshift/community/networking/submariner/06-show-status.sh
./openshift/community/networking/submariner/08-verify-connectivity.sh
```

Step 07 labels the peer worker and joins that peer to the London broker. Both
cluster identities and all CIDR combinations are checked first. The same cluster
selected twice, overlapping ranges, changed cluster UIDs or mismatched APIs stop
the workflow. The peer's CNI remains OVN-Kubernetes.

Step 08 runs upstream connectivity and service-discovery suites with disruptive
tests disabled. It uses separate explicit kubeconfig/context arguments, so
identical context names in the two installation files cannot select the wrong
cluster. The verifier creates temporary test workloads/RBAC on participating
clusters; review and account for those writes before running it. The private
JUnit result is `verify.xml` under the networking state directory.

Acceptance: remote connections connected in both directions, verifier suites
pass, and existing OKD workloads remain healthy. A mesh failure must not be
reported as database/site failover success. Namespace separation and a working
mesh do not automatically replicate MySQL data or change MySQL Router behavior.

## Diagnostics and private state

```bash
./openshift/community/networking/submariner/09-gather-diagnostics.sh
```

The default state directory is
`$ADMIN_WORKDIR/internal_doc/networking/submariner`. It holds the pinned client,
cluster UID receipts, `broker-info.subm`, broker ownership receipt and test/log
outputs. Broker files contain sensitive credentials/IPsec material. Keep the
folder mode 700, files private and every artifact out of Git. Do not publish a
support archive without reviewing it for credentials and infrastructure details.

| Problem | Action |
| --- | --- |
| London installation review is false | Complete and validate OKD first; do not bypass the health gate. |
| No eligible London worker | Add a SCOS worker, then select its exact name. |
| OVN compatibility unverified | Check the exact release and NB DB schema; preserve a private evidence record. |
| Gateway label/resource version changed | Re-read identity/state; retry after review, rather than removing other gateway labels. |
| Broker already exists without credentials | Recover/review broker state manually with upstream guidance; do not redeploy a second broker. |
| No remote connection in London-only mode | Expected until the peer phase is explicitly enabled and completed. |
| Tunnel down after peer joins | Check UDP 4500, ESP, DRG routes, reachable gateway IPs, MTU and broker API access. |
| DNS test fails with connected tunnel | Inspect Lighthouse pods, ServiceExport/ServiceImport and DNS forwarding/policies. |
| Existing deployment detected on join retry | Inspect step 06/09 results; this first cut does not upgrade or reinstall. |

Uninstallation and gateway-failover disruption tests are intentionally manual
upstream procedures for a separate maintenance window; no cleanup command here
removes an installed network from a running cluster.

References: [upstream prerequisites](https://submariner.io/getting-started/),
[subctl commands](https://submariner.io/operations/deployment/subctl/),
[usage and service discovery](https://submariner.io/operations/usage/),
[release assets](https://github.com/submariner-io/releases/releases/tag/v0.24.2),
[known issues](https://submariner.io/operations/known-issues/).
