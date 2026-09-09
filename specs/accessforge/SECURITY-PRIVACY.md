# AccessForge — Security, privacy and abuse model

Version 1.0 · 9 September 2026 · Required controls and verification work, not a completed security assessment.

## 1. Assets, actors and boundaries

Protect customer source, test credentials, reader observations, synthetic/consented fixture data, desktop input authority, evidence integrity, approval records and repository publication privileges. Treat websites, issue text, source repositories, dependencies, generated patches, uploaded artifacts and model output as hostile inputs. An authenticated member can still be malicious or accidentally operate in the wrong workspace.

Trusted components are the correctly configured host administrator, identity/control plane, sealed evaluator and admitted runner supervisor. The local owner-operated E0 boundary is not equivalent to a managed multi-tenant sandbox. Compromise of a desktop host can forge observations; hash chains do not remove that trust assumption. Do not onboard arbitrary customer builds before R1 isolation and tenant tests pass.

Enforcement must exist outside an agent's prompt. The navigator's process has neither privileged credentials nor a tool/network path to the shell, source, DOM or observer oracle. The repair/build worker has no control-plane writes, protected evaluator mount, runner credentials or GitHub publication secret. A proxy that can be bypassed through an equally privileged direct API does not enforce the policy. AgentCore, if used, protects only the paths actually routed through its configured controls; document direct-path denial separately.

## 2. Threat and control matrix

| Threat | Required control | Proof obligation |
|---|---|---|
| Website/source prompt injection | Treat text as data; explicit trusted tool registry; no target-repository tool autoloading; constrained worker identity/network. | Inject requests to read secrets/change policy/use shell; verify denial at real boundary, not only model refusal. |
| Cross-workspace ID substitution | Membership and composite resource predicates at API, SQL, jobs, runner assignment, objects, streams and exports; RLS defense in depth. | Valid caller from workspace A attempts every workspace B surface and receives no data or action. |
| Stale or broadened approval | Digest/revision/scope/expiry binding and dispatch-time revocation check; server-owned policy. | Approve then alter patch/base/effects, expire or revoke; dispatch must fail. |
| Desktop escape or unintended effects | Dedicated session, narrow action/key allowlist, origin/focus checks, safe test fixtures, no arbitrary navigation/download/clipboard. | Attempt OS shortcuts, redirect to unapproved origin and real submission; record block without executing effect. |
| Runner partition/restart duplicate action | Durable local intent journal, lease epoch, local expiry watchdog, quarantine/reset and fresh run after ambiguity. | Kill after key dispatch but before result; no blind resend or concurrent replacement actor. |
| Evidence tampering or omission | Bound manifests, source hash chain, contiguous sequence, validated objects and conservative outcome rules. | Modify/delete/fork/reorder/stale-submit evidence; verified outcome is withheld. |
| False successful repair | Protected evaluator/assertions/fixtures/backend policy; matched independent rerun and functional/security regression gates. | Patch removes validation, bypasses auth or changes oracle; reject even if journey appears easier. |
| Build supply-chain compromise | Disposable isolated worker, scoped readonly source, controlled dependencies, no host mounts/socket, reviewed install scripts. | Malicious build tries metadata endpoint, credential files, service API and neighboring tenant volumes; deny. |
| SSRF through project or artifact URLs | Scope-specific URL parsing, redirect revalidation, DNS/IP checks and destination-policy egress enforcement. | Test rebinding, encoded IPs, redirects, metadata/link-local and forbidden ports. |
| Stored XSS through evidence/diffs | Escape text, sanitize allowed rich text, restrictive CSP, separate artifact origin and attachment disposition. | HTML/SVG/script payload cannot execute in authenticated UI context. |
| Forged/replayed GitHub webhook | Verify raw-body signature, size/time limits, installation scope and durable delivery deduplication. | Invalid HMAC, duplicate delivery, revoked install and fork code cannot publish or receive credentials. |
| Unbounded cost/storage/run creation | Atomic entitlement admission, per-workspace quotas, model/action/wall-time budgets and bounded ingestion. | Concurrent requests cannot exceed admitted cap; budget stop records real state rather than synthetic success. |
| Sensitive evidence leak | Minimal collection, redaction before model/export where possible, private objects, short-lived scoped downloads and log filtering. | Search exported objects, traces, URLs and error logs for seeded canary secrets. |

Local/staging addresses may legitimately be private; implement separate explicit environment policies rather than a blanket “allow all internal networks.” The runner must not be usable as a general proxy into that network. Each approved origin is constrained to the application and task-specific endpoints. DNS validation alone is not sufficient if the connection can subsequently target a different address.

## 3. Identity and authorization

Owners manage workspace membership, scope, retention and entitlements. Maintainers configure authorized projects and request approved work. Reviewers read authorized evidence and record review; they do not receive execution or publication authority merely by reviewing. Viewers are read-only. Implementation must produce an explicit route/action role matrix and tests rather than infer permission from UI visibility.

Use secure browser sessions with CSRF protection, rotation and logout/revocation behavior. Machine identities are short lived, audience scoped and bound to workspace/run/lease as appropriate. An enrollment token is one time and expires quickly; do not place long-lived tokens in frontend storage, URLs, manifests or exports. Revalidate permissions during long streams and queued execution. Signed object URLs remain usable until expiry if already issued; make this residual window short, document it, and use authenticated download proxying where immediate revocation is required.

Consequential approvals display exact target and effect. `RUN_EFFECTS` permits only the frozen test task; `PATCH_APPLY` only an isolated candidate; `GITHUB_PUBLISH` only the approved payload/repository. None implies merge, production deployment, payment, terms acceptance or external application submission. The system records an authorization denial even when the UI is stale or the model asks repeatedly.

## 4. Data lifecycle

| Class | Default handling | Export/model exposure |
|---|---|---|
| Secrets and authentication state | Managed secret references, least privilege, rotation; never ordinary evidence. | Never intentionally included. Stop and report canary/secret detection. |
| Fixture values | Synthetic, resettable, run-specific and minimal. | Only safe task values to navigator; never hidden answers. |
| Reader observations | Capture only needed task trace; identify incidental data risk and obtain collection consent. | Redact approved categories; show any reduced verification scope. |
| Source code/diffs | Scoped files at an exact identity; private storage and approved model-provider use. | Minimum necessary source spans; publication separately authorized. |
| Screenshots/audio/video | Off unless needed and explicitly consented; no unrelated desktop capture. | Separate consent, audience and redaction; actual media only, clearly attributed. |
| Review/study records | Minimum reviewer attribution; no forced disability disclosure. | Review scope can be shared with authorization; study identity/compensation stays separate. |
| Audit/usage metadata | Minimized operational fields and configurable justified retention. | Aggregate where possible; no raw prompts/secrets in telemetry. |

Before a pilot, choose and disclose concrete retention periods per data class and model-provider handling. This package does not invent customer-approved retention or legal compliance. No indefinite retention by omission. Deletion is an asynchronous tracked operation with scope, status, failures, backups/replica expiry and a completion report. Where raw evidence is deleted, retain only lawful minimal metadata and mark evidence verification limitations. Do not silently reconstruct deleted sensitive content from a model log or search index.

Export redaction creates a derived artifact and a new digest, never a forged raw hash. Export manifests identify omitted/redacted/deleted objects and their effect on independent verification. Public demonstration uses synthetic data and explicit source/media permissions; personal browser notifications and unrelated windows must not be captured.

## 5. Abuse limits and incident response

No production purchases, real applications, messages to real recipients, CAPTCHA bypass or indiscriminate third-party crawling in E0/R1. Only authorized test environments and safe resettable effects. Do not grant a generic “agent can do anything” permission to make a journey run. An unsupported task becomes a visible blocked capability request.

Incident procedure: stop new admission, revoke affected identities/approvals, fence and quarantine runners, isolate compromised build workers, preserve necessary evidence under approved retention, identify affected workspace/object scope, communicate known facts and uncertainty, rotate secrets, repair and independently retest. Never resume an ambiguous physical action while recovering. Restore drills must prove obsolete leases and tokens are unusable.

Pre-pilot release requires real adversarial checks across identity, database, network, runner, build, evidence and UI boundaries. An external specialist review is appropriate before broad customer exposure; this specification and an LLM review are not equivalent to an independent security assessment. See [TEST-PLAN.md](TEST-PLAN.md), [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md), and [TDD.md](TDD.md).
