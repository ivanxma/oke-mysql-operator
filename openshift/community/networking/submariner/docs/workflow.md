# Two independent OKD clusters with Submariner — London-first lab

Run this workflow **after the London OKD installation has passed acceptance**.
It retains OVN-Kubernetes and adds Submariner connectivity/Lighthouse discovery.
It does not install OKE, replace the CNI, replicate storage/databases or automate
application failover. Its optional gateway-network step creates dedicated gateway NSGs only; it makes
no shared security-list, route-table or IAM changes.

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
  do not grant broad workload SCC privileges manually. The verifier uses temporary,
  namespace-only nonroot-v2 access for its own test service accounts as described below.

### Network preparation — review before running

Use OKD step `08-prepare-worker-network.sh` to prepare a compartment-scoped node
NSG for OVN Geneve before manually creating workers; attach its reported NSG to
each worker. This does not supply the cross-cluster tunnel rules.

Have the networking owner prepare approved DRG routes and private DNS. Step
03-prepare-gateway-network.sh prepares scoped gateway rules in dedicated NSGs.
These scripts do not run `subctl cloud prepare` or alter shared NSGs/security lists.

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

From the cloned repository root, select the existing generated installation and
initialize networking:

```bash
source ./generated/openshift/uk-london-1/jumphost/00-env.sh
./openshift/community/networking/submariner/00-initialize.sh
cd generated/openshift/uk-london-1/jumphost/networking/submariner
```

Step 00 reads the actual API, cluster UID, CIDRs, health and eligible workers. It
creates private `submariner.env`, `submariner.optional.env`, numbered launchers
and discovery state beside the selected installation profile. It does not change
Kubernetes or OCI. No Mac file upload is required. Existing settings are retained;
rerun after a worker joins to fill an empty generated gateway when exactly one
eligible worker is discovered. A control-plane node is never selected automatically.

Use the generated networking folder for subsequent commands. The launchers select
the existing shared environment and `KUBECONFIG`; `~/.kube/config` is never used.
The mandatory profile contains the pinned version, unique target ID, gateway and
review settings. Optional peer kubeconfig/API/machine CIDR/gateway/context settings
are kept separately. Keep `SUBMARINER_ENABLE_PEER=false` until the peer is ready.

Review the local network requirements, then run step 02 with
`--record-reviewed-prerequisites` to explicitly acknowledge that review. This mode
checks the pinned client/server version diagnostic, live OVN schema and cluster
health, then records the review flags in private configuration. The default step
02 validates without changing configuration. Remote tunnel paths remain unverified
until the separate peer-network review. Discovery does not certify compatibility or
prove remote routing. All sensitive settings and broker credentials remain private.

## Phase 1 — London only

```bash
./01-install-subctl.sh
./02-validate-environment.sh --record-reviewed-prerequisites
./03-label-london-gateway.sh
./04-deploy-london-broker.sh
./05-join-london.sh
./06-show-status.sh
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

Full two-site connectivity requires Submariner on both clusters. First join the
peer worker using OKD step 13, then pass peer OKD steps 06 and 11. Refresh the
generated networking launchers after updating the clone by rerunning the public
00 initializer from the selected London profile. Existing credentials are retained.

From the generated London networking folder:

```bash
./02-register-peer.sh --peer-region eu-frankfurt-1
./03-prepare-gateway-network.sh          # Read-only preview
./03-prepare-gateway-network.sh --apply  # Dedicated gateway NSGs only
./03-verify-gateway-paths.sh             # Read-only SSH/route/ping/TLS probes
```

Step 02 registers the peer kubeconfig/API, unique cluster ID, machine CIDR and
exact worker hostname from its generated regional profile and live API. Both
clusters must be healthy, identities distinct and CIDRs disjoint. It also checks
the peer Kubernetes version diagnostic and OVN Northbound schema. Only one peer
worker is selected automatically; ambiguous inventories stop for review. No
Kubernetes/OCI changes are made by registration. Enrollment is enabled, but the
separate peer-network review stays false until explicitly acknowledged.

Step 03 verifies enrolled worker/OCI VM/image identities, compartment ownership
and existing subnet DRG routes for both gateways before any mutation. It creates
a dedicated NSG per gateway and attaches only that worker VNIC, preserving other
memberships with ETag guards. Rules are stateful and restricted to the exact peer
private /32: UDP 4500, ESP protocol 50 and ICMP echo for diagnostics. Existing
shared security lists must preserve required ICMP fragmentation-needed delivery
for path MTU. No shared list, route, resolver or IAM is changed.

Review actual gateway paths, broker TCP 6443 reachability and MTU. Rule readback
is not proof of packet delivery. Then explicitly record the review and enroll:

```bash
./02-register-peer.sh --peer-region eu-frankfurt-1 --record-reviewed-network
./02-validate-environment.sh
./07-join-peer.sh
./06-wait-for-mesh.sh --timeout 600 --interval 15
./06-show-status.sh
./08-verify-connectivity.sh
./08-check-verification.sh
```

The registration writes actual selections to private optional configuration and
sets the peer-enable flag. It retains separate kubeconfigs; no default kubeconfig
is used. It does not install a peer operator or reuse the London worker image.

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

The pinned upstream verifier fixes test container UID to 10000, which falls
outside OKD's allocated project UID range. Step 08 streams native progress and
recognizes only namespaces created by that verifier process. It checks fresh
creation time, upstream test labels and namespace UID before creating a namespaced
RoleBinding for that namespace's default service account to the built-in
`nonroot-v2` SCC role. This permits nonroot test containers; it does not grant
privileged/anyuid access, alter SCC definitions or touch application accounts.
Bindings are recorded privately and removed with UID guards after the run; test
namespace deletion also removes them. Existing namespaces from earlier runs are
rejected. Native verifier cleanup retains responsibility for test workloads.
See [the pinned upstream framework](https://github.com/submariner-io/shipyard/blob/v0.24.2/test/e2e/framework/framework.go).


Acceptance: remote connections connected in both directions, verifier suites
pass, and existing OKD workloads remain healthy. A mesh failure must not be
reported as database/site failover success. Namespace separation and a working
mesh do not automatically replicate MySQL data or change MySQL Router behavior.

## Diagnostics and private state

```bash
./09-gather-diagnostics.sh
```

The default state directory is
`$OKD_STATE_DIR/networking/submariner`. It holds the pinned client,
cluster UID receipts, `broker-info.subm`, broker ownership receipt and test/log
outputs. Explicitly configured legacy state remains in place. Broker files contain sensitive credentials/IPsec material. Keep the
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

## Repeatable path, rollout and evidence checks

`03-verify-gateway-paths.sh` repeats the preparation probes using discovered
worker addresses and generated regional SSH state. It first checks live OCI
VM/image/compartment bindings and existing DRG routes without changing them,
then verifies SSH instance metadata/hostname, the private route, three DF pings
with 1400-byte payloads, and broker HTTPS using the configured API CA. It sends
no administrative credentials to the workers. HTTP 401/403 establishes a reached
authorization endpoint; it is not proof of administrative access. This is a path
check before join; UDP/ESP and workload traffic are verified after enrollment.

`06-wait-for-mesh.sh` gives elapsed time and component/gateway status until local
readiness or both gateways are active and connected to the expected private peer
IP and cluster ID. The wait is bounded and never rejoins or restarts components.
Use `--timeout` and `--interval` to set polling limits. A timeout leaves deployment
running; diagnose or resume the wait rather than reinstalling.

`08-check-verification.sh` checks the latest run's JUnit report against its start
time and recorded cluster UIDs. It distinguishes skipped specs from failures,
requires passing tests and zero suite/test errors, and checks that run-owned test
namespaces and temporary SCC RoleBindings are gone on both clusters. It waits up to 120 seconds for asynchronous test namespace cleanup, printing
remaining namespace/grant counts; use `--wait 0` for a single snapshot or
`--wait <seconds> --interval <seconds>` for a different bounded wait. It never
forces deletion. It writes `verification-check.json` privately and makes no
cluster changes. A new test run
archives the previous XML and binds its receipt to both cluster identities, so a
previous passing report cannot satisfy a failed or incomplete rerun. Older
receipts without this binding stay as historical evidence; rerun step 08 for new
acceptance. The report check establishes recorded-test and current-cleanup status;
step 06 supplies current gateway readiness.

Manual prerequisites remain: approved compartment IAM, shared DRG/DNS routing
where required, manual OCI VM launches from the imported cluster-specific images,
and the explicit network-review acknowledgement. Discovery and probes replace
manual copying of addresses and IDs; they do not silently change IAM or shared
networking. The automated recipe is for the supported two-region lab; gateway HA
and disruptive failover tests need a separate maintenance plan.

## Broker client reload after restart

If gateways are connected but ServiceExports report broker TLS errors, compare
that with the broker CA and token currently stored in each selected spoke's
`submariner-broker-secret`. Run `06-restart-service-discovery.sh` for a preview.
The script verifies saved cluster identities and performs a certificate-verified,
authenticated broker ServiceImport read using those existing credentials.

With `--apply`, it recycles only the UID-bound Lighthouse agent pod at each site
and waits for a new Ready pod. The Submariner operator reconciles its Deployment,
so a Deployment restart annotation alone is insufficient evidence of a restart.
The helper changes no CA/token, tunnel, CNI, OCI resources or private DNS records.
Then verify ServiceExport readiness and remote workload DNS. A successful reload
is not proof that imports exist: destination namespaces must also exist.
