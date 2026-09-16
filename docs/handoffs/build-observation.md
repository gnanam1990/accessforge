# Offline build identity collection

On a supported POSIX operator host with the workspace dependencies installed:

```sh
uv run python -m accessforge_persistence.build_observation \
  --repo /absolute/path/to/dedicated-checkout \
  --artifact /absolute/path/outside-checkout/actual-build.tar \
  --revision HEAD
```

The command prints the existing build-registration JSON body to stdout. It does not contact the
API, load a session, build source, extract an archive, upload bytes or grant execution authority.
Review the observations before using them in the journey's build registration form or the existing
`POST /v1/workspaces/{workspace_id}/projects/{project_id}/builds` operator workflow.
Do not copy dummy digests or arbitrary archives to make preparation succeed.

Use a stable dedicated checkout of the actual source used for the artifact. A revision pointing
elsewhere is refused rather than paired with the current checkout. Ignored bytes participate in
the tree digest and therefore prevent a clean-commit claim. The artifact must be a regular file
outside the source checkout; no-follow/nonblocking file reads are required. Source status and
artifact identity changes observed during collection are refused. This is not an atomic filesystem
snapshot or proof that the artifact was actually built from the source: the trusted build workflow
must establish that causal relationship and retain the bytes separately.

`identityObservable` deliberately remains false. A local hash cannot prove which artifact a target
deployment serves. Only set the existing registration field true after observing that exact target
identity through the deployment's supported identity surface. Registration, canonical sealing,
execution approval and run admission remain separate actions.
