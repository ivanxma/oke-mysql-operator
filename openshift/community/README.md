# Community OKD on OCI Compute

Start on the **Oracle Linux 9 jump host**. Install one private SCOS control-plane
VM, with optional SCOS workers. This community workflow needs no Red Hat account.

## Prerequisites

- An OL9 jump host with sudo, Internet access and an existing protected SSH key
  that can authenticate back to this host. Step 01 installs the remaining tools.
- An authorized instance principal, compartment, private node subnet and DNS
  domain. See [network/IAM prerequisites](docs/installation.md).

## 00. Clone and generate the working folder

Run in Bash as your normal user. Clone once:

```bash
sudo dnf install -y git python3 openssh-clients
git clone https://github.com/ivanxma/oke-mysql-operator.git okd-install
cd okd-install
```

For an existing clone, run `git pull --ff-only` from its root instead of cloning
again.

**Generate the execution folder**, from the cloned repository root:

```bash
./openshift/community/00-create-workspace.sh --region uk-london-1
```

This creates `<clone>/internal_doc/regions/uk-london-1/jumphost/` with the private
configuration and numbered launch scripts. `jumphost` is the default folder name;
no manual `mkdir` is needed. After successful generation, enter it:

```bash
cd internal_doc/regions/uk-london-1/jumphost
```

An existing private folder is reused. Resume the next unfinished step.

The generator creates the private configuration and numbered launch scripts.
It reuses an existing profile, or asks for missing settings. Region and compartment
ID are discovered from OCI metadata when available; cluster name and overlay
CIDRs have planning defaults. Review those ranges against connected networks.
For EU, select `eu-frankfurt-1` and enter the corresponding generated folder.

**Run all remaining commands from this generated folder, one at a time.** Each
launcher selects its configuration automatically. No manual configuration-file
creation or transfer is required. No target VM/IP is needed during preparation.

## 01–04. Prepare and import the image

```bash
./00-check-config.sh --preparation
./01-prepare-host.sh
./02-create-bucket.sh
./02-download-okd.sh
./02-download-scos-image.sh
./03-build-control-plane-image.sh
./04-upload-image.sh
```

Stop on failure. Wait for the custom image to be **`AVAILABLE`**.

## Create the new VM in OCI

Manually launch a **new private VM** from that image in the selected region and
compartment. Lab baseline: compatible x86 shape, **4 OCPU / 48 GB RAM / 150 GB
boot**, UEFI, Secure Boot disabled, no public IP. Booting installs SCOS on the
new VM's configured installation disk. Record the actual assigned private IP.

## 05. Register, configure DNS and validate

```bash
read -r -p "Actual new VM private IP: " node_ip
./05-register-node-ip.sh "$node_ip" --assigned-to-target
unset node_ip
./01-prepare-host.sh --ssh-only
./05-discover-dns-view.sh
./05-configure-dns.sh                  # Read-only preview
./05-configure-dns.sh --apply          # Changes OCI DNS
./05-verify-dns.sh --wait 600           # Read-only polling on both hosts
./00-validate-environment.sh --existing-target
```

DNS discovery is saved automatically. Require **8 PASS, 0 FAIL** for DNS, then
resolve environment failures and review warnings. Publication/cache refresh can
take time. DNS must work during bootstrap; preserve installation state on retries.

If TARGET reports a changed SSH host identity after first boot, stop polling and
follow [target host-key recovery](docs/operations.md#target-host-key-recovery).
Confirm the registered IP belongs to your intended new VM, then run
`./05-refresh-target-host-key.sh --apply`. It backs up saved identities, removes
only that target entry and prompts you to accept its replacement key. Then repeat
DNS verification and validation. Console fingerprint verification is optional.

## 05–11. Complete and accept

```bash
./05-wait-for-control-plane.sh
./06-check-control-plane.sh
./11-verify-web-access.sh
```

Require **`RESULT: INSTALLER COMPLETED`**, then steps 06 and 11 to pass.
`--status-only` takes one snapshot; returning to the shell is not completion.
To resume later, return to the generated folder and run the next unfinished step.

If API requests time out or operators stop progressing, run
`./05-diagnose-control-plane.sh` (read-only). Follow the
[bootstrap diagnostic and recovery sequence](docs/operations.md#bootstrap-api-and-installer-diagnostics).
Do not rebuild the image to retry monitoring.

## Details and optional work

- [Generated folder and configuration options](docs/configuration.md)
- [Status, CLI/browser, workers, demo and ISO cleanup](docs/operations.md)
- [Network diagram](docs/architecture.md)
- [Submariner](networking/submariner/README.md) · [MySQL Operator](../mysql-operator/README.md)
- [Sample mandatory env](community.env.example) · [Optional settings](community.optional.env.example)

Generated configuration, state and credentials stay in Git-ignored `internal_doc/`.
