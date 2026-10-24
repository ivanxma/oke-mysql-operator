# Video: MySQL ClusterSet across London and Frankfurt OKD

Watch [the narrated demo video](mysql-clusterset-two-regions.mp4), or open
[the chapter player](index.html). [Captions](mysql-clusterset-demo.vtt) and a
[transcript](transcript.txt) are included. The narrated MP4 is about **5 minutes
43 seconds**, at **2880 × 1620**, with English captions.

**Recording mode:** real read-only OKD console captures with private fields
masked, followed by a clearly labelled terminal replay with illustrative output.
Cluster creation, demo SQL writes and the Frankfurt switchover are instructional
scenes; they were not performed on the live deployment for this video.

## What the video shows

- London at the left, Frankfurt at the right, and native MySQL Shell ClusterSet
  operations in the center.
- A local `mysql-operator` deployment in the same-named namespace on each OKD
  cluster, separate from `submariner-operator` networking/discovery workloads.
- Submariner Gateway state, ServiceImport discovery and the OKD DNS Operator's
  `clusterset.local` forwarding entry.
- London `cluster1` creation, then Frankfurt `cluster2` bootstrap through
  `initDB.clusterSet` and the exported primary member hostname.
- PRIMARY/REPLICA roles, `super_read_only`, a demo write/read, controlled switchover
  to Frankfurt and verification of the reversed replication direction.

Lighthouse builds Kubernetes service DNS answers from ServiceExport,
ServiceImport and EndpointSlice metadata. It does **not** manage OCI private DNS
zones for API, internal API or application ingress names. Submariner carries
cross-cluster traffic; each MySQL Operator reconciles local resources; MySQL
ClusterSet manages asynchronous database replication and global roles.
The one-member-per-region topology is a lab and has no local database redundancy.

To use chapter navigation in a browser, serve this folder locally:

```bash
python3 03-serve-demo.py
```

Open `http://127.0.0.1:8000/`. The local player supports chapter seeking.
The MP4 can also be played or shared directly.

## Example commands and manifests

[Manifests](manifests/) match the illustrative terminal sequence. They assume
compatible local MySQL Operators, namespace/SCC grants, worker labels, regional
storage and protected root Secrets already exist. They contain no passwords.
The example Submariner IDs are `london` and `frankfurt`; replace them with the
selected generated configuration for a real installation. Advertised member
hostnames must resolve in both clusters before replica bootstrap.

Use these examples for a fresh, isolated lab. The video renderer never executes
these manifests or any displayed Kubernetes/MySQL command. For the existing
installation, use its generated profiles and the authoritative
[regional installation guide](../mysql-operator/README.md),
[restart health runbook](../docs/restart-health.md) and
[network workflow](../networking/submariner/README.md).
Do not apply illustrative manifests over existing database identities.

## Render the video locally

From this folder, use a Python virtual environment with the listed dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python 01-render-demo.py
```

The default font is DejaVu Sans Mono. Use `--font` and `--mono-font` to select
installed TrueType/OpenType fonts when needed. `--preview-only` writes chapter
images without encoding the movie. `--voice <installed-voice>` adds narration
using the local macOS `say` command; without it the output is captioned and silent.
Run `python 02-check-demo.py` to check assets, duration, codecs and full decoding.
`--work-dir` chooses a temporary rendering folder; `--output-dir` chooses where
the MP4, poster, captions, transcript and chapter index are written.

Public screenshots are redacted presentation assets. Browser sessions, passwords,
raw captures and deployment evidence stay outside this folder under ignored
private documentation. New console capture requires authorized read-only access;
no credential or live endpoint is embedded in the renderer or storyboard.

References: [Lighthouse discovery](https://submariner.io/getting-started/architecture/service-discovery/),
[MySQL ClusterSet status](https://dev.mysql.com/doc/mysql-shell/26.10/en/innodb-clusterset-status.html),
[controlled switchover](https://dev.mysql.com/doc/mysql-shell/26.10/en/innodb-clusterset-switchover.html).
