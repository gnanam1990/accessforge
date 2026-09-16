# Railway control-plane deployment

The root Dockerfile builds the web with frozen pnpm dependencies and installs
non-editable Python packages with frozen uv dependencies. It serves web and API
from one origin, as a non-root user. No desktop runner, model CLI, migration or
background execution worker starts with the web process. The Docker build context
excludes local environments, credentials, dependencies and existing build outputs.

Use a dedicated project with PostgreSQL and a private Railway S3-compatible bucket.
Set `PORT=8080` and `ACCESSFORGE_PORT=8080` together (the image's fixed port).
The runtime requires these private Railway variables:

- `ACCESSFORGE_DATABASE_URL`: application role, never PostgreSQL superuser credentials.
- `ACCESSFORGE_EVIDENCE_ENDPOINT_URL`: HTTPS bucket endpoint.
- `ACCESSFORGE_EVIDENCE_BUCKET`, `ACCESSFORGE_EVIDENCE_ACCESS_KEY`,
  `ACCESSFORGE_EVIDENCE_SECRET_KEY`: dedicated bucket credentials, never build arguments.
- `ACCESSFORGE_ENVIRONMENT=production`, `ACCESSFORGE_HOST=0.0.0.0`.
- `ACCESSFORGE_IDENTITY_PROVIDER=none` until dedicated GitHub OAuth is configured.

Initialize a fresh database separately using the versioned migrations. Existing
databases require a backup and explicit migration approval first. Use a separate
schema-owning migration role; the web role must be NOSUPERUSER/NOBYPASSRLS with
only schema usage and required table/sequence access. Never migrate on replica
startup. `/health/ready` checks schema compatibility, database connectivity and
object-store TCP reachability, not full S3 authorization or reader readiness.

Deploy only a clean, retained source snapshot, not a directory containing operator
secrets or evidence. The Railway readiness gate is `/health/ready`; use one replica
initially. No GitHub autodeployment is required: deploy explicitly after normal
source review, and retain required exact-head CI before merging deployment changes.

For public login follow [GitHub identity setup](../handoffs/github-user-login.md).
Never switch to passwordless local-development identity on a public listener.
Configure the real callback domain and verify proxy-header trust and callback-log
redaction before enabling OAuth. Account binding and workspace membership are
separate operator tasks; possession of a GitHub account is not workspace access.

A healthy hosted web service is not actual VoiceOver/NVDA or repair/rerun/human
review acceptance. Those remain attached to separately qualified desktop runners.

References: [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles),
[storage buckets](https://docs.railway.com/storage-buckets),
[health checks](https://docs.railway.com/deployments/healthchecks).
