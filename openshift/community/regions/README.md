# Regional OKD profiles

`uk-london-1/` and `eu-frankfurt-1/` select separate community OKD installations
using the shared scripts in the parent `openshift/community/` folder. They do not install OKE.

Follow the [short OKD installation guide](../README.md).
Technical prerequisites, configuration and troubleshooting are in
[the installation docs](../docs/installation.md).

From the repository root, prepare one private profile:

```bash
./openshift/community/regions/uk-london-1/00-init-config.sh
${EDITOR:-vi} internal_doc/regions/uk-london-1/community.env
source ./openshift/community/regions/uk-london-1/env.sh ""
./openshift/community/regions/uk-london-1/00-check-config.sh --preparation
```

Then run that region's numbered wrappers in the same order as the shared guide.
Replace the region folder when selecting another site. Each profile has its own
workspace, kubeconfig, SSH state and generated registrations. Real values belong
in Git-ignored `internal_doc/`; private keys remain in their protected key folder.

Sample files: [mandatory environment](../community.env.example)
and [optional overrides](../community.optional.env.example).
