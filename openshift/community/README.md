# Community OKD on OCI Compute

Install one SCOS control-plane VM, with optional workers. Use **Oracle Linux 9
for the admin/jump host**; cluster nodes run **SCOS**, not OL9. This community
workflow uses a public OKD release and requires no Red Hat account.

Run the steps below in **Bash from `openshift/community/`**. Stop on a failed step.
A new cluster needs a separate workspace. Keep real configuration and credentials
in Git-ignored `internal_doc/`.

## 1. Configure

Choose `uk-london-1` or `eu-frankfurt-1`:

```bash
region=uk-london-1
../../regions/$region/00-init-config.sh
${EDITOR:-vi} ../../internal_doc/regions/$region/community.env
source ../../regions/$region/env.sh ""
../../regions/$region/00-check-config.sh --preparation
```

Use [mandatory settings](community.env.example) and, if needed,
[optional settings](community.optional.env.example). Fill the mandatory private
profile before sourcing it. **No target VM or node IP is needed yet.**

## 2. Prepare and import the installation image

```bash
./01-prepare-host.sh
./02-create-bucket.sh
source ../../regions/$region/env.sh ""
./02-download-okd.sh
./02-download-scos-image.sh
./03-build-control-plane-image.sh
./04-upload-image.sh
```

Wait for the custom image to be `AVAILABLE`.

## 3. Create the VM in OCI

Manually create a **new private VM** from that imported image in the selected
region and compartment. Use a compatible x86 shape, UEFI and a suitable boot
volume. See [VM and network prerequisites](docs/installation.md). Record the
actual assigned private IP. Booting the image installs SCOS on its target disk.

## 4. Register the VM and configure DNS

```bash
read -r -p "Actual VM private IP: " node_ip
./05-register-node-ip.sh "$node_ip" --assigned-to-target
unset node_ip
source ../../regions/$region/env.sh ""
./01-prepare-host.sh --ssh-only
./05-discover-dns-view.sh
./05-configure-dns.sh                 # Read-only preview
./05-configure-dns.sh --apply         # Creates/updates OCI DNS
./05-verify-dns.sh --wait 600          # Read-only DNS polling on both hosts
./00-validate-environment.sh --existing-target
```

Discovery saves the view automatically. No OCID copying is required. DNS must
work during bootstrap; allow publication/cache refresh before validation.
Resolve failures and review warnings before continuing.

## 5. Wait and accept the cluster

```bash
./05-wait-for-control-plane.sh
./06-check-control-plane.sh
./11-verify-web-access.sh
```

Wait for **`RESULT: INSTALLER COMPLETED`**, then require steps 06 and 11 to pass.
`--status-only` takes one snapshot; returning to the shell does **not** mean the
installation completed. See [status and troubleshooting](docs/operations.md).

## Next steps

- [CLI, browser, demo, workers and ISO cleanup](docs/operations.md)
- [Configuration and private state](docs/configuration.md)
- [Network diagram and service placement](docs/architecture.md)
- [Independent clusters with Submariner](networking/submariner/README.md)
- [MySQL Operator on OKD](../mysql-operator/README.md)
