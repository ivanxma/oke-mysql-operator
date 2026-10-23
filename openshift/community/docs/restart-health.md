# Health checks after a regional restart

Use this runbook for the existing two-region OKD, Submariner and MySQL ClusterSet
lab. Run on the administration host from the cloned repository. Checks use the
existing generated profiles; no addresses, credentials or OCI identifiers belong
in this document. A VM restart preserves the installation and its volumes.

## 1. Choose the restart scenario

| Restarted site | Expected temporary impact | Check order |
| --- | --- | --- |
| Frankfurt only | Replica, its Router and gateway unavailable; the London primary can continue serving if otherwise healthy. Replication must catch up after Frankfurt returns. | Check London for impact; wait for Frankfurt OKD/CSI; check mesh/DNS, Frankfurt MySQL and the complete ClusterSet. |
| London only | The configured writer and the Submariner broker are unavailable. Frankfurt remains a replica; restarting London does not promote it. Existing DNS cache entries do not prove broker synchronization. | Wait for London OKD/CSI and broker; check mesh/DNS; recover the existing primary only if necessary; verify Frankfurt and replication. |
| Both regions | Both local database groups and the cross-region path are unavailable. | Restore the administration host and control planes, then workers. Accept OKD/CSI in each region, broker/mesh/DNS, the existing primary, then the replica and complete ClusterSet. |

These roles describe the installed lab. If a planned switchover or failover has
changed the primary, use the live ClusterSet metadata and the incident record;
do not assume the configured London `PRIMARY` profile is still authoritative.
Keep an outage open while either site is unavailable, even if the other site's
local checks pass. No automatic database promotion is part of this procedure.

## 2. Select one region and validate its identity

**Administration host · Bash · local environment selection; no resource changes.**
Run within the clone. Use a separate terminal for the other region, changing
`REGION` to `eu-frankfurt-1`. Use the existing deployment folder name if it differs
from the example `jumphost`.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
REGION=uk-london-1
DEPLOYMENT=jumphost
SITE_DIR="$REPO_ROOT/generated/openshift/$REGION/$DEPLOYMENT"
source "$SITE_DIR/mysql-operator/00-env.sh"
test -s "$KUBECONFIG"
oc config current-context
oc config view --minify -o jsonpath='{.clusters[0].cluster.server}{"\n"}'
printf 'Expected API: %s\n' "$MYSQL_EXPECTED_API_SERVER"
```

Stop if the selected API differs. Once the API responds, also verify its saved
cluster UID before any recovery action:

```bash
actual_uid=$(oc --request-timeout=10s get namespace kube-system \
  -o jsonpath='{.metadata.uid}')
if [[ $actual_uid == "$MYSQL_OKD_UID" ]]; then
  echo 'PASS: selected cluster identity matches the generated profile.'
else
  echo 'STOP: API unavailable or cluster identity differs; do not mutate resources.'
fi
unset actual_uid
```

Do not continue on a failed check. The exported `KUBECONFIG` must exist; do not use
an implicit default kubeconfig or copy credentials from another cluster. If the
administration VM restarted, check its clock, disk space and access to both APIs.
A normal restart does not require replacing SSH host fingerprints or certificates.

## 3. Wait for local OKD and storage in each region

**Read-only · run in each regional terminal.** Start with a bounded API request:

```bash
oc --request-timeout=10s get --raw='/readyz?verbose'
```

If it times out, the API is still unavailable. Retry after 15–30 seconds while
checking VM power state, private DNS and the administration route. Stop and
investigate if availability does not improve; do not rerun installation media,
VM replacement or cluster creation to fix a restart.

After the API responds, wait for the node and operator conditions:

```bash
oc wait --for=condition=Ready node --all --timeout=15m
oc wait --for=condition=Available clusteroperator --all --timeout=15m
oc wait --for=condition=Progressing=False clusteroperator --all --timeout=15m
oc wait --for=condition=Degraded=False clusteroperator --all --timeout=15m
oc wait --for=condition=Updated machineconfigpool --all --timeout=15m
oc wait --for=condition=Updating=False machineconfigpool --all --timeout=15m
oc wait --for=condition=Degraded=False machineconfigpool --all --timeout=15m
"$SITE_DIR/06-check-control-plane.sh"
"$SITE_DIR/11-verify-web-access.sh"
```

Each timeout means **NOT READY**, not completion. The waits show the condition
being awaited; step 06 supplies the final node/operator snapshot. Require the
profile's expected node count, every node Ready, every operator Available with
Progressing/Degraded false, and updated non-updating machine-config pools.
Step 11 verifies console HTTPS from the administration host; workstation browser
routes and DNS require a separate client check.

Verify the existing CSI components and database volumes before database recovery:

```bash
oc -n oci-csi rollout status deployment/csi-oci-controller --timeout=10m
oc -n oci-csi rollout status daemonset/csi-oci-node --timeout=10m
oc -n oci-csi get pods -o wide
oc -n "$MYSQL_NAMESPACE" get pvc
oc -n "$MYSQL_NAMESPACE" get pods -o wide
```

Require all desired CSI replicas ready and existing data claims Bound. Temporary
`NetworkNotReady`, CSI registration and mount warnings can occur during startup;
compare current pod conditions with event timestamps. Repeated new mount errors,
Pending PVCs or attachment conflicts are failures to investigate. Preserve the
existing claims and volumes. Reinstalling the storage class is not a restart step.

## 4. Verify the cross-region path from the London profile

Both APIs must be reachable before running the two-site helpers. They deliberately
stop when the peer is unavailable; that does not establish that the surviving
site has a local fault.

**Read-only · administration host · London networking execution folder.**

```bash
cd "$REPO_ROOT/generated/openshift/uk-london-1/$DEPLOYMENT/networking/submariner"
./06-wait-for-mesh.sh --timeout 600 --interval 15
./06-show-status.sh
```

Require both gateways active, expected private peer connections `connected`, and
all desired Submariner Deployments/DaemonSets ready. This proves current component
and tunnel readiness, not service discovery or database replication. If it times
out, inspect the affected region using its selected environment:

```bash
oc -n submariner-operator get pods -o wide
oc -n submariner-operator get gateways.submariner.io -o yaml
oc -n submariner-operator logs deployment/submariner-lighthouse-agent --tail=40
oc get dns.operator.openshift.io/default -o jsonpath='{.spec.servers}{"\n"}'
```

A disconnected tunnel requires gateway/route/MTU and peer security-rule checks.
A broker TLS/authentication error requires separate broker-client diagnostics.
Use the existing [network workflow](../networking/submariner/docs/workflow.md);
do not rejoin an already enrolled cluster or change shared OCI networking.

## 5. Verify discovery and inspect MySQL locally

**Read-only · run in both regional terminals after sourcing step 2 again if needed.**

```bash
oc get namespace mysql-cluster1 mysql-cluster2
oc -n "$MYSQL_NAMESPACE" get serviceexports.multicluster.x-k8s.io -o yaml
oc -n mysql-cluster1 get serviceimports.multicluster.x-k8s.io cluster1-instances
oc -n mysql-cluster2 get serviceimports.multicluster.x-k8s.io cluster2-instances
oc -n "$MYSQL_NAMESPACE" get innodbcluster,pods,pvc -o wide
oc -n mysql-operator logs deployment/mysql-operator --tail=40
```

Both namespace names must exist at both sites. Require each source ServiceExport
`Ready=True`, remote ServiceImports and matching EndpointSlices. In OKD,
`dns.operator.openshift.io/default` should forward `clusterset.local` to the local
Lighthouse DNS service. Do not confuse these records with OCI API/apps DNS.

When the London MySQL process is listening and both operators are running, run
this from either generated MySQL folder:

```bash
"$SITE_DIR/mysql-operator/11-verify-service-discovery.sh"
```

It must pass from **both** operator pods: exported primary pod hostname resolves
and TCP 3306 returns a MySQL protocol greeting. It does not require Group
Replication to be ONLINE. If the process is not listening yet, allow local MySQL
startup or diagnose its logs, then repeat; do not treat that TCP failure as a
reason to rewrite correct DNS records.

Inspect a direct local database session, entering the private MySQL password at
the prompt. **The following login and queries are read-only.**

```bash
oc -n "$MYSQL_NAMESPACE" exec -it "${MYSQL_CLUSTER_NAME}-0" \
  -c sidecar -- mysqlsh --js --uri root@127.0.0.1:3306 --password
```

Inside MySQL Shell:

```javascript
var c = dba.getCluster();
c.status({extended: 1});
var cs = c.getClusterSet();
cs.status({extended: 1});
```

If `dba.getCluster()` cannot obtain a healthy group, inspect SQL directly:

```sql
\sql
SELECT @@global.super_read_only;
SELECT MEMBER_HOST, MEMBER_STATE, MEMBER_ROLE
FROM performance_schema.replication_group_members;
```

Leave MySQL Shell with `\quit` before running Bash commands below.

A running mysqld and Ready OKD node do not imply ONLINE Group Replication. Both
one-member groups can report local member role PRIMARY while only one has global
ClusterSet role PRIMARY. In the installed roles, London is writable and Frankfurt
must remain read-only. Connect to Frankfurt's direct pod to verify it; its Router
can send traffic to the global primary. `OK_NO_TOLERANCE` is expected for this
one-member lab and indicates no local database failure tolerance.

## 6. Recover only a confirmed persistent fault

Skip this section when the corresponding health checks already pass. The commands
below are explicitly separated into previews and changes.

### Lighthouse client reports broker TLS errors

From the London networking folder, **preview: read-only TLS/token/identity checks**:

```bash
cd "$REPO_ROOT/generated/openshift/uk-london-1/$DEPLOYMENT/networking/submariner"
./06-restart-service-discovery.sh
```

If the existing broker CA and credentials verify and the client reload is needed,
**change: recycle the selected Lighthouse agent pod in each enrolled cluster**:

```bash
./06-restart-service-discovery.sh --apply
```

This helper operates on both enrolled sites, even after a one-site restart. It
waits for replacement agent pods; it does not replace broker trust or restart the
gateway. The operator can reconcile a Deployment restart annotation away, so
`rollout restart` returning success alone is not proof the agent restarted.
Repeat discovery checks afterward. If peer namespaces/exports are missing,
inspect their ownership before using MySQL step 05 in each affected regional
profile. **Change: restore the managed peer namespace and local ServiceExport**:

```bash
"$SITE_DIR/mysql-operator/05-prepare-service-discovery.sh"
```

This is an apply operation, not a preview. Stop if ownership differs; correct
records do not need re-creation after every restart.

### Existing one-member London primary remains OFFLINE

First confirm that London is still the intended ClusterSet primary, its storage
is healthy, its MySQL process is reachable, and the single member is OFFLINE.
A changed global primary, invalidation, divergent transactions or multi-member
outage requires a separate database recovery review.

From the London generated MySQL folder, **preview: native recovery dry run**:

```bash
cd "$REPO_ROOT/generated/openshift/uk-london-1/$DEPLOYMENT/mysql-operator"
./12-recover-primary.sh
```

After a successful preview, **change: recover the existing local group**:

```bash
./12-recover-primary.sh --apply
./07-wait-cluster.sh
```

The helper is restricted to the configured one-member primary and uses native
MySQL Shell recovery without force. It is not a promotion or replica recovery.
Router can remain in restart backoff briefly until the database becomes ONLINE;
wait rather than rotating Router credentials.

### Frankfurt's existing local group remains OFFLINE

The primary-only helper refuses this role. With the global primary healthy,
connect to Frankfurt's direct sidecar as in step 5. Confirm complete local group
outage and review metadata/transaction state. In MySQL Shell JavaScript mode,
**preview** the native recovery:

```javascript
\js
dba.rebootClusterFromCompleteOutage('cluster2', {dryRun: true});
```

If the dry run succeeds and database recovery has been reviewed, **change**:

```javascript
dba.rebootClusterFromCompleteOutage('cluster2');
```

Use the actual local name from the selected profile if different. Native recovery
checks ClusterSet eligibility and can restore its replication channel; then repeat
all acceptance checks. Do not add force, remove metadata or re-create a PVC to
bypass a failure. See the [native major-outage procedure](https://dev.mysql.com/doc/mysql-shell/26.10/en/reboot-outage.html).

## 7. Accept the complete application after either restart scenario

**Read-only · run in each generated MySQL folder.**

```bash
"$SITE_DIR/mysql-operator/07-wait-cluster.sh"
"$SITE_DIR/mysql-operator/10-audit-storage.sh"
```

Step 07 waits for ONLINE member count and ready database/Router rollouts, then
checks worker-only placement. Step 10 reads actual PVC/PV/OCI volume and worker
attachment scope; it uses instance principals and saves a private report.

**Read-only · run from the London generated MySQL folder.**

```bash
cd "$REPO_ROOT/generated/openshift/uk-london-1/$DEPLOYMENT/mysql-operator"
./11-verify-service-discovery.sh
./08-verify-clusterset.sh
```

Require ClusterSet **HEALTHY**, both clusters' global status OK, expected
PRIMARY/REPLICA roles, primary writable and replica `super_read_only=1`.
The helper expects the original lab roles; after an intentional role change,
inspect native ClusterSet status and use an updated acceptance plan instead of
forcing these configured expectations. Native replication channel details are
available through [extended ClusterSet status](https://dev.mysql.com/doc/mysql-shell/26.10/en/innodb-clusterset-status.html).

For a fresh end-to-end acceptance test, **change: write a small retained probe row
on the primary and verify it directly on the replica**:

```bash
./09-test-replication.sh
```

This is not read-only. A previous row present at both sites does not prove that
new transactions are replicating after the restart. Replication is asynchronous;
allow catch-up and require the new probe to pass before reporting tested-write
acceptance. If writes are not permitted, report read-only health only and mark a
new write/read test NOT PERFORMED.

If stronger network acceptance is needed, from the London networking folder,
**change: create temporary verification workloads and test-only SCC grants**:

```bash
cd "$REPO_ROOT/generated/openshift/uk-london-1/$DEPLOYMENT/networking/submariner"
./08-verify-connectivity.sh
./08-check-verification.sh --wait 120
```

The second command is read-only: it validates that run's JUnit report and cleanup.
Require passing tests, zero failures and removal of run-owned namespaces/grants;
review skipped cases. An old passing report is historical evidence, not proof
that connectivity recovered after this restart. See [Submariner troubleshooting](https://submariner.io/operations/troubleshooting/).

## 8. Report an explicit outcome

| Layer | Required current evidence |
| --- | --- |
| Regional OKD | Expected nodes Ready; operators settled; pools updated; verified console HTTPS. |
| Storage | CSI ready; existing PVCs Bound; audited regional volumes attached to the intended workers. |
| Submariner | Both gateways active/connected, expected components ready. |
| Discovery | Exports Ready, imports/endpoints visible, exported member DNS and MySQL greeting pass from both sites. |
| MySQL | Local clusters ONLINE, database/Router pods ready, intended global roles, healthy replica channel and read-only replica. |
| New-write acceptance | Fresh primary probe observed directly on the replica, or explicitly NOT PERFORMED. |

Record the restart scope, timestamps, per-region results, any recovery actions,
new-write test status and unresolved warnings under the private generated state
folder or the private incident runbook. Keep addresses, credential paths, logs,
resource identifiers and database output out of public commits. Timeouts, a
returned shell prompt or one healthy region do not establish two-site readiness.
