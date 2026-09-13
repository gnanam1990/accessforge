# Original-source comparison preparation

The repair workspace currently exposes replacement/deletion text but cannot invent original base
content. This adds the trusted source-preparation side of the missing unified comparison.

`accessforge_build_worker.comparison.prepare_comparison` reads the current actor's evidence-read
membership, exact proposal and authorized sealed baseline under workspace RLS. It reconstructs the
original source through the existing Git object broker, using only an operator-provisioned
project-to-repository map. The broker verifies commit objects, content tree and modes without
checkout, hooks, filters, source scripts, fetch, package installation or model invocation. Dirty
user checkouts remain untouched; a dirty sealed snapshot is refused rather than substituted.

The comparison binds patch/revision/digest, original manifest/tree/commit/archive, workspace,
project, source snapshot and requesting actor in a canonical comparison digest. Only proposed
paths leave the preparation function. Each file includes original/proposed UTF-8 text, byte count,
SHA-256, mode, ADD/MODIFY/DELETE semantics, an explicit no-op flag and unified diff. Mode-only changes
and missing final LF remain explicit. Git LF boundaries are not confused with Unicode separators
or bare CR. Quoted path headers prevent a filename from injecting additional diff headers.

Preparation is bounded to 20 files, 2 MiB combined before/after text, 4000 lines per side and 20000
lines total. Oversized, missing, malformed, binary, protected or unsupported source is refused;
there is no partial/truncated comparison with a claim of completeness. This is comparison data,
not an applied candidate, build receipt, functional verification or approval.

The explicit operator command is:

```text
python -m accessforge_build_worker.compare_command --workspace-id WORKSPACE_UUID --project-id PROJECT_UUID --patch-id PATCH_UUID --actor-id ACTOR_UUID --repository OPERATOR_REPOSITORY --include-source
```

Use canonical UUIDs and an already provisioned repository; these uppercase placeholders are not
executable project identities. The trusted host supplies `ACCESSFORGE_DATABASE_URL`. Source output
to stdout requires `--include-source`; omission refuses before repository/database access. Error
output excludes source, repository paths and connection strings. No DB row, artifact or application
file is written, and no provider/build/reader is dispatched. The command was not executed against
user data during development.

Changed-file Ruff/mypy and diff checks are local validation. CI-only fixtures cover all file-change
kinds, exact line endings, absent/binary/budget refusal and a real owned Git repository whose dirty
checkout must survive unchanged. These tests were authored, not locally run. DB-bound operator
runtime acceptance, durable comparison retention/read API and integration into the browser's
unified/plain-text repair view remain unfinished next steps. Physical repair verification and the
full project goal remain unproven.
