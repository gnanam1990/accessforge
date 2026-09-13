# Reader startup consent — storage and HTTP delivery

Separate branch: `feat/reader-startup-consent`, includes PR37 correction6d244ab. Not part of PR37.

Implemented locally: migration0038 and persistence methods for exact per-run operator consent,
reviewable pinned Guidepup0.34.0 effect scope, infrastructure-operator attribution, bounded expiry,
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
routes; generated clients contain92 operations.

Pending: operator UI, native client/bootstrap consumption, deeper migration/tenant/lifecycle
coverage and deployed operator workflow. Existing low-level action endpoints do not implicitly
enforce this new startup path; this is not yet an end-to-end physical startup gate.

Changed Python Ruff/mypy passed. Seven focused HTTP grant/refusal/binding/revocation cases and
machine-vs-human OpenAPI checks are authored for CI only. Forward-migration expectations include
0038; no local tests or migration were executed. No actual consent, OS permission or reader startup.
