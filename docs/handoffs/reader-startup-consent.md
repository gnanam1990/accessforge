# Reader startup consent — storage and HTTP delivery

Separate branch: `feat/reader-startup-consent`, includes PR #37 correction 6d244ab. Not part of PR #37.

PR #37 merged into main at 30ce2f0; this branch is restacked onto that commit. PR #38 baseline a45c15e
passed all applicable CI in 34743969093. The following normal-review corrections need fresh CI:
direct consent deletion is refused while its run/workspace remain, with parent cascades preserved;
unusable seals are normalized to consent refusal instead of 500; supervisor operations are excluded
from the human cookie/CSRF operation tables (all server paths remain in PATHS and native machine
transport remains supported); supervisor OpenAPI no longer promises a human-limiter 429 response.
Scoped deletion/cascade, stale-environment and contract checks are authored for CI, not run locally.

Implemented locally: migration 0038 and persistence methods for exact per-run operator consent,
reviewable pinned Guidepup 0.34.0 effect scope, infrastructure-operator attribution, bounded expiry,
irreversible revocation and one-time binding to an authenticated execution session. Run, runner,
physical desktop key, profile, manifest and effects digest must remain identical; run approval is
separately required. Binding uses service audit attribution, not a fabricated human action.

No consent is issued by this work. The dedicated-desktop acknowledgement is a human statement,
not physical evidence or permission to enable OS/TCC settings. Normal target deletion follows the
existing run cascade; audit events remain under the existing audit-retention policy.

HTTP delivery adds four human routes: scope review (with runnerId and run-revision ETag), stored
inspection, explicit grant (owner/CSRF/If-Match/exact effects and desktop acknowledgement), and
irreversible exact-consent revocation. Idempotent issue replies describe the original stored
decision, not current authority; revocation remains possible after run revision changes.

The machine-only empty-body reader-startup-consent route authenticates the private session and
live execution authority before binding/rechecking consent in the same transaction. Its expiry
cannot exceed either consent or execution authority. Browser cookies/bootstrap reuse refuse.
OpenAPI now correctly describes all supervisor routes as bearer-authenticated, not browser-cookie
routes; the published contract contains 92 operations, with machine-only operations excluded from
the human clients' callable operation tables.

Native delivery requires an independently provisioned exact consent/manifest/desktop/profile
scope. The pinned SDK effects digest must match locally, and bootstrap freezes scope before any
claim/network/SDK operation. Initialization uses the machine consent endpoint before SDK startup
and after startup/postflight; every reply must match all bindings and can only shorten its deadline.
Failed, expired, revoked or mismatched replies fence further input without a cached grant fallback.
The independent host authorization callback and all physical readiness gates remain mandatory.

Pending: operator UI, deeper migration/tenant/lifecycle coverage and deployed operator workflow.
Existing low-level action endpoints do not implicitly enforce this new startup path. Production
profile eligibility still refuses unproven matrices before claim/network/SDK; no actual reader
execution or end-to-end deployed proof was performed.

Changed Python Ruff/mypy passed. Seven focused HTTP grant/refusal/binding/revocation cases and
machine-vs-human OpenAPI checks are authored for CI only. Forward-migration expectations include
0038; no local tests or migration were executed. No actual consent, OS permission or reader startup.
Desktop TypeScript build passed. Eight scoped native HTTP/snapshot cases are authored for CI only,
reusing the existing synthetic local-HTTP fixture rather than adding a physical/browser suite.
