# MySQL ClusterSet across regional OKD clusters

This lab deploys `cluster1` in London and the asynchronous `cluster2` replica in
Frankfurt, using the existing Submariner mesh. Each has one database member and
one Router on its region's labelled worker; this is not a three-member HA design.
The existing same-OKD workflow under `openshift/mysql-operator` remains separate.

Prerequisites: both OKD clusters and the Submariner mesh accepted, one Ready worker
per site, and compartment-scoped instance-principal Block Volume/attachment rights.
Run on the jump host from a full clone. No workstation configuration upload is needed.

From the clone root, generate both regional execution folders. Enter the chosen
lab password without putting it in shell history:

```bash
read -r -s -p 'Lab MySQL administrator password: ' mysql_password; echo
printf '%s\n' "$mysql_password" | ./openshift/community/mysql-operator/00-create-workspace.sh --lab --password-stdin
unset mysql_password
```

Run steps 01–05 in **each** generated folder:

```bash
cd generated/openshift/uk-london-1/jumphost/mysql-operator
./01-prepare-admin.sh
./02-label-worker-nodes.sh
./03-install-storage-class.sh
./04-install-mysql-operator.sh
./05-prepare-service-discovery.sh
```

Repeat in `generated/openshift/eu-frankfurt-1/jumphost/mysql-operator`.
Step 03 reuses the selected OCI class or installs scoped CSI and `mysql-oci-bv`.
It may drain/reboot the selected worker for device naming; complete one region at
a time. Database PVCs request 50Gi, use `Retain` and are never silently deleted.

Create London first, then Frankfurt:

```bash
# From London's generated MySQL folder:
./06-create-cluster.sh
./07-wait-cluster.sh
# From Frankfurt's generated MySQL folder:
./06-create-cluster.sh
./07-wait-cluster.sh
# Back in London's generated MySQL folder:
./08-verify-clusterset.sh
./09-test-replication.sh
# In each regional MySQL folder, audit its volume/worker attachment:
./10-audit-storage.sh
```

Every launcher selects its regional OKD environment and exported `KUBECONFIG`.
Private configuration, credentials, manifests and receipts stay under the selected
regional generated folder. No implicit `~/.kube/config` is used.

See [technical details](docs/deployment.md) for cross-cluster addressing, security,
storage, expected results and recovery boundaries.
