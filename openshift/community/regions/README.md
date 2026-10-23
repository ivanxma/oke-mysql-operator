# Regional OKD profiles

`uk-london-1/` and `eu-frankfurt-1/` select separate community OKD installations
using the shared scripts in the parent `openshift/community/` folder. They do not install OKE.

Follow the [short OKD installation guide](../README.md).
Technical prerequisites, configuration and troubleshooting are in
[the installation docs](../docs/installation.md).

The normal jump-host workflow generates a private execution folder with
`../00-create-workspace.sh`; follow the installation guide above and run numbered
commands inside that generated folder.

For advanced use of source-level regional wrappers, prepare a private profile
from the repository root:

```bash
./openshift/community/regions/uk-london-1/00-init-config.sh
${EDITOR:-vi} generated/openshift/uk-london-1/community.env
source ./openshift/community/regions/uk-london-1/env.sh ""
./openshift/community/regions/uk-london-1/00-check-config.sh --preparation
```

Then run that region's numbered wrappers in the same order as the shared guide.
Replace the region folder when selecting another site. Each profile has its own
workspace, kubeconfig, SSH state and generated registrations. Real values belong
in Git-ignored `generated/openshift/`; private keys remain in their protected key folder.

Sample files: [mandatory environment](../community.env.example)
and [optional overrides](../community.optional.env.example).

## Prepare another region from the same jump host

The jump host's region and `OCI_REGION` for the deployment may differ. Generate
a separate profile with `00-create-workspace.sh --region eu-frankfurt-1`; select
the authorized target subnet/compartment and run steps 01–04 inside that generated
folder. Each region has its own bucket, media, kubeconfig and custom image. VM
creation remains manual; register its actual IP afterward with step 05.

For two generated deployments with the same folder name, private DNS zones can
be published in both existing VCN views after both actual node IPs are registered:

```bash
./05-sync-regional-dns.sh --peer-region uk-london-1          # Preview from Frankfurt
./05-sync-regional-dns.sh --peer-region uk-london-1 --apply  # Cluster DNS changes
./05-verify-dns.sh --wait 3600
```

The sync verifies both VMs/images, compartments, subnets and views. It changes only
the selected clusters' three A sets in private zones, preserving other records.
It does not change shared resolvers, DNS forwarding or routes. DRG peering alone
does not share private DNS views. Resolver caches still need time to refresh.
DNS verification probes a fresh wildcard before fixed API names, avoiding new
fixed-name negative cache entries while a private zone is still being published.
