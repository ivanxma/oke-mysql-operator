# Regional OKD profiles

`uk-london-1/` and `eu-frankfurt-1/` select separate community OKD installations
using the shared scripts in `openshift/community/`. They do not install OKE.

Follow the [short OKD installation guide](../openshift/community/README.md).
Technical prerequisites, configuration and troubleshooting are in
[the installation docs](../openshift/community/docs/installation.md).

From the repository root, prepare one private profile:

```bash
./regions/uk-london-1/00-init-config.sh
${EDITOR:-vi} internal_doc/regions/uk-london-1/community.env
source ./regions/uk-london-1/env.sh ""
./regions/uk-london-1/00-check-config.sh --preparation
```

Then run that region's numbered wrappers in the same order as the shared guide.
Replace the region folder when selecting another site. Each profile has its own
workspace, kubeconfig, SSH state and generated registrations. Real values belong
in Git-ignored `internal_doc/`; private keys remain in their protected key folder.

Sample files: [mandatory environment](../openshift/community/community.env.example)
and [optional overrides](../openshift/community/community.optional.env.example).
