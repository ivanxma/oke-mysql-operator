# Submariner between independent OKD clusters

Run this optional lab after OKD acceptance and worker provisioning. Start with
one cluster, then explicitly enable peer enrollment. The scripts retain
OVN-Kubernetes. The optional gateway-network step changes only dedicated,
compartment-owned NSGs; shared routes/lists and storage/databases are unchanged.

Run on the Linux admin host. Follow the [technical workflow](docs/workflow.md)
for private configuration, network prerequisites and the exact command order.

| Step | Action |
| --- | --- |
| 00 | Discover live settings and generate a private networking execution folder. |
| 01 | Download and verify the pinned `subctl` tool. |
| 02 | Validate cluster identities, versions, CIDRs and prerequisites. |
| 03 | Explicitly label the selected worker gateway. |
| 04 | Deploy the London broker. |
| 05 | Join London. |
| 06 | Inspect status or wait for gateway rollout and private peer connections. |
| 07 | Explicitly enroll the peer when approved. |
| 08 | Verify connectivity/discovery, then check the report and cleanup. |
| 09 | Gather private diagnostics. |

A single enrolled cluster cannot demonstrate a remote tunnel. Actual credentials,
addresses, inventories and diagnostic archives stay outside public source.

From the clone root after selecting the generated OKD environment:

```bash
source ./generated/openshift/uk-london-1/jumphost/00-env.sh
./openshift/community/networking/submariner/00-initialize.sh
cd generated/openshift/uk-london-1/jumphost/networking/submariner
./01-install-subctl.sh
```

Step 00 discovers CIDRs and eligible workers. A single control-plane node is not
used as the gateway by this workflow. Provision a cluster-matched SCOS worker
with OKD steps `09-build-worker-image.sh` and `10-import-worker-image.sh`, then
rerun initialization after it joins. After reviewing local network prerequisites, step 02 with
`--record-reviewed-prerequisites` checks versions/OVN/health and records the private
review settings. Peer gateway networking needs its own review before enrollment.

During the first-cluster stage, `06-show-status.sh` requires healthy component
rollouts, an active selected worker gateway and only local endpoints. It reports
`LOCAL DEPLOYMENT READY` with zero remote connections expected. This confirms
local installation; cross-cluster connectivity requires peer enrollment and step 08.

When the second cluster and its worker pass OKD acceptance, register its generated
profile with `02-register-peer.sh --peer-region eu-frankfurt-1`. Then preview/apply
`03-prepare-gateway-network.sh` for exact-peer private IPsec NSGs. Follow the
[peer phase](docs/workflow.md#phase-2--enroll-the-peer-only-when-ready) to review
paths, join the peer and test connectivity. Live values are discovered from the
selected generated profiles.

For a fresh peer installation, use this order after peer discovery:

```bash
./03-prepare-gateway-network.sh --apply
./03-verify-gateway-paths.sh
./02-register-peer.sh --peer-region eu-frankfurt-1 --record-reviewed-network
./02-validate-environment.sh
./07-join-peer.sh
./06-wait-for-mesh.sh --timeout 600
./08-verify-connectivity.sh
./08-check-verification.sh
```

Path probes and readiness/report checks change no cluster/OCI resources; they
save private evidence. Step 08 verification creates temporary test resources.
For an existing joined pair, resume at the readiness check; do not rerun join.
