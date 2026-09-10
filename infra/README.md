# `infra/`

Deployment preparation, the declared version matrix, and the proposed AWS configuration.

| Path | What it is |
|---|---|
| [`version-matrix.toml`](version-matrix.toml) | The versions this release was actually built and tested against, plus the platform capabilities no version number can establish. `scripts/doctor.py` checks a machine against it; `tests/unit/test_version_matrix.py` checks it against the repository's own pins, so a dependency bump that forgets it fails. |
| [`aws/`](aws/README.md) | **PREPARED, never applied.** A written proposal describing exactly what would be created, and the IAM policies each component would need. No AWS resource has been created by anything in this repository. |

Operating procedures live in [`docs/operations/`](../docs/operations/):
[DEPLOYMENT.md](../docs/operations/DEPLOYMENT.md),
[BACKUP-AND-RESTORE.md](../docs/operations/BACKUP-AND-RESTORE.md),
[RESOURCES-AND-COST-DRIVERS.md](../docs/operations/RESOURCES-AND-COST-DRIVERS.md),
[RUNBOOKS.md](../docs/operations/RUNBOOKS.md).

Local development needs a running PostgreSQL 17 and an S3-compatible object store. Run
`uv run python scripts/doctor.py` first: it checks the whole environment in one pass, installs
nothing, and prints no credential.
