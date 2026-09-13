# Repair workspace: actual proposals and exact decisions

The old repair screen said the patch and verification APIs did not exist. It now reads those
implemented APIs and shows stored proposal identity, base manifest/tree digests, rationale, complete
replacement/deletion content, separate dependency/build scope, attached approval details and all
recorded verification attempts. Finding pages expose repair-history navigation on request.

The API supplies after-content, not original base-file text. This slice therefore does not invent
a unified diff or deleted lines. A native read-only text control exposes complete proposed text;
an escaped alternative preserves line endings and exposes directional control characters. One
selected file is rendered at a time. Obtaining retained original base content and producing the
full accessible unified comparison remain unfinished work, not satisfied by this content viewer.

Owner/maintainer PATCH_APPLY approval is separate from owner/reviewer rejection, matching the
server's actual role matrix (maintainers do not inherit reviewer authority). Preview retains the
exact patch revision/digest, initially unchecked acknowledgement, bounded expiry and explicit
isolated-candidate-only effect. Approval requires the human to inspect the exact base separately
because this page does not supply it. Binary/symlink/unsupported-mode records cannot be approved.
No action invokes a build, edits application source, approves a GitHub merge or deploys anything.

The existing native Dialog retains keyboard focus/restoration behavior. A pending decision cannot
be double-submitted or dismissed as if the server rolled it back. Every write carries If-Match and
CSRF; failure/unknown response closes the stale preview and reconciles the original record by GET,
without implicit mutation retry. Resource loading/permission/offline failures are not empty history.
Run, patch, candidate-verification and human-review states remain separately labelled.

Patch GET now exposes the attached approval's scope, actor, exact target/digest/revision, UTC expiry
and revocation, with no-store on patch and history reads. An attached approval or APPROVED status
does not imply the worker can still dispatch it. Fresh dispatch authority remains server-owned.

Validation: web TypeScript/production build, changed-file Ruff/mypy and diff checks. Focused API
readback and frontend cases are authored for CI only, including stale identity, hostile source,
unknown decision response, role boundaries and unchanged INCONCLUSIVE verification. No local test
suite, actual browser/reader acceptance, actual patch approval or deployment was performed.

Remaining: real original/candidate unified diff and evidence-detail comparison, model-generated
proposal delivery, production build/reader provisioning, physical matched proof and release work.
