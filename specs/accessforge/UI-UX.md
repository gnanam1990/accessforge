# AccessForge — UI/UX specification

Version 1.0 · 9 September 2026 · Proposed interface; not an implemented or accessibility-certified application.

## 1. Product experience

Build a calm, precise engineering workspace where a person can answer: **What prevented this journey, what changed, and what evidence establishes the result?** The main product is a working investigation and repair flow, not a landing page or chat window. Use a restrained modern visual system, generous spacing, excellent typography and evidence-first interaction. Avoid decorative graphs, synthetic metrics, glass panels over low-contrast backgrounds and “AI fixed everything” celebratory states.

The UI must remain useful without animations, screenshots, color differentiation or a mouse. AccessForge's own onboarding, run inspection and review are first-class actual-screen-reader test journeys. Automated checking alone does not establish their accessibility. The product-wide rules below were informed by the `ui-ux-pro-max` skill; visual choices are design proposals that require implementation-time contrast and interaction verification.

## 2. Design tokens and components

| Token family | Specification |
|---|---|
| Typography | Fira Sans UI with system sans fallback; Fira Code for code, digests and aligned numbers. Self-host licensed font assets or use the fallback. Body 16px/1.6; secondary 14px/1.5; page title 28–32px; no essential information only in tiny labels. |
| Light palette | Background `#F8FAFC`, surface `#FFFFFF`, foreground `#1E293B`, secondary text `#475569`, primary `#2563EB` with white foreground, destructive `#DC2626` with white foreground. These are candidate pairs, not a substitute for measured contrast. |
| Dark palette | Background `#0F172A`, surface `#1E293B`, foreground `#F8FAFC`, secondary `#CBD5E1`, primary accent `#93C5FD` with `#0F172A` foreground. Validate every final text, control, focus and status pair independently. |
| Structure | 4px spacing scale; common 8/12/16/24/32px intervals; 8–12px corner radius; subtle borders; one restrained elevation level for overlays. |
| Interaction | Clear default, hover, focus, pressed, busy, disabled and read-only states. Aim for comfortable 44px web control hit areas; never use native pt/dp as CSS units. |
| Focus | Persistent high-contrast 3px outline with offset; ensure focused controls are not obscured by sticky panels. Distinguish keyboard focus from selection. |
| Motion | Typically 200–250ms opacity/transform transitions; instantaneous essential state feedback; disable nonessential motion with reduced-motion preference. No correctness dependent on animation completion. |
| Icons | One consistent SVG set, e.g. Lucide. Decorative icons hidden from accessibility APIs; icon-only buttons have explicit accessible names. Prefer visible action text. |

Build shared Button, Link, StatusBadge, Notice, FormField, ErrorSummary, Dialog, Tabs, DataTable, Pagination, CodeDiff, EvidenceList, EmptyState, LoadingState and PermissionBoundary components before composing screens. Component previews must show long text, zoom, both themes, keyboard focus, errors and pending states. Use tested primitives where appropriate; audit actual generated semantics and avoid unnecessary custom ARIA widgets.

Each screen has one primary action. Approval, cancellation, deletion and publication are separate named actions with consequence-specific dialogs, not generic “Confirm” buttons. Required explanatory text cannot exist only inside a tooltip. Toasts are supplementary: important errors and outcomes remain on the page until resolved or intentionally dismissed.

## 3. Information architecture

Use a labelled sidebar at desktop widths, a compact labelled navigation drawer on narrow screens, and a consistent project switcher. The workspace header includes breadcrumbs, current workspace, search and account controls. Authentication and permission state are visible but never expose another tenant's names.

| Route pattern | Main content | Primary action |
|---|---|---|
| `/w/:workspaceId/overview` | Real recent runs, unresolved findings, runner availability and required attention. | Open a relevant project; create project if empty. |
| `/w/:workspaceId/projects` | Authorized projects, environment labels and last tested revisions. | Add project. |
| `/w/:workspaceId/projects/:projectId` | Repository/environment scope, active journey versions and recent runs. | Create or select journey. |
| `/w/:workspaceId/projects/:projectId/journeys/:journeyId` | Draft/frozen versions, intent, fixture profile, assertions, limits and validation. | Freeze version or request run, depending on state. |
| `/w/:workspaceId/runners` | Actual profile, preflight, lease, heartbeat and quarantine/reset state. | Enroll or inspect runner. |
| `/w/:workspaceId/runs/:runId` | Manifest, execution state, outcome, ordered replay and evidence completeness. | Investigate finding or open candidate comparison. |
| `/w/:workspaceId/findings/:findingId` | Supported observation, source links, reproduction and repair history. | Propose repair when eligible. |
| `/w/:workspaceId/patches/:patchId` | Exact diff, approval scope, build, regressions and matched verification. | Approve isolated candidate or review verified repair. |
| `/w/:workspaceId/reviews/:reviewId` | Machine evidence and separate human decision with scope/limitations. | Submit review. |
| `/w/:workspaceId/exports/:exportId` | Export preparation, redaction, verification limits and download. | Request or download export. |
| `/w/:workspaceId/settings` | Membership, integrations, retention, budgets, schedules and manual entitlement. | Save the active settings section. |

URLs are stable deep links, not authority tokens. The server authorizes every request. Filter/sort/page state lives in the URL where nonsensitive; task fixture values, tokens and secret references must not enter browser history. Back navigation restores list position and filters. A genuine route change focuses the main heading; background events do not move focus.

## 4. Onboarding and journey authoring

Onboarding is a short sequence: authorize project → bind environment/build → enroll and preflight runner → create journey → review scope. Show requirements before requesting OS permissions. Explain why a dedicated signed-in desktop is required; never instruct users to let an unattended agent take over their daily desktop.

The environment editor labels Local and Staging explicitly. Display the exact permitted origin, repository revision, reset strategy, credential profile and permitted test effects. Connection success is not authorization to perform a run. Separate saved configuration from current validation, with last checked time and invalidation reason.

Journey authoring has a readable form as the primary surface and a validated structured view as an optional expert surface. Fields: intent, safe fixture profile, supported runner profile, required assertions, independent completion condition, permitted effects, budgets and out-of-scope behaviors. A Strands-generated draft is labelled “Suggested draft—review required”; it cannot freeze or grant itself execution authority.

After failed submission, move focus once to a focusable error summary with links to affected controls; retain specific inline errors connected via `aria-describedby`. Preserve entered values. Do not repeatedly steal focus on blur. Freezing a version shows a concise immutable-input summary. Editing after freezing creates a draft successor; it does not silently alter previous runs.

## 5. Run and evidence replay

The core desktop view has a main evidence timeline and a narrower manifest/assertion panel. On small screens use one column with explicit section navigation. Each timeline entry shows sequence, source, action or observation, time and provenance. Source time is useful context; sequence determines ordering. Distinguish agent proposal, supervisor-admitted action, actual reader observation and independent observer result using text labels.

Do not build the only usable replay as a visual canvas. Provide a semantic ordered list, keyboard-operable event selection, search/filter, transcript download and an unvirtualized paginated reading mode. For large traces, virtualization is an optional visual optimization with an equivalent complete accessible mode. Announce a contextual summary such as “Run interrupted; 1 unresolved action” rather than reading every streamed token. Users can pause automatic following without pausing the run.

Manifest details show complete digests on request with copy buttons and their full accessible names. Long IDs wrap; do not destroy ordinary word boundaries. Audio or video is optional and labelled absent when unavailable; never synthesize a voice track and present it as actual screen-reader evidence. Where media exists, provide transcript, playback controls and privacy warnings. Redacted content shows an explicit reason and evidence impact.

Status and outcome are separate fields:

| Situation | Required copy and behavior |
|---|---|
| QUEUED / NOT_EVALUATED | “Waiting for an eligible runner.” Show queue age and missing capabilities. |
| RUNNING / NOT_EVALUATED | Show current admitted step, budget and cancellation control. No provisional green success badge. |
| FINALIZING | “Validating evidence.” Distinguish upload progress from evaluation. |
| COMPLETED / PASS | “This journey passed on the listed build and profile.” Show untested scope. |
| COMPLETED / FAIL | Show the exact false required assertion and supporting evidence. |
| COMPLETED / INCONCLUSIVE | Show missing/invalid/unknown evidence and an actionable next step. |
| INTERRUPTED | “Execution interrupted; retry starts a new run.” Show ambiguous effects separately. |
| Cancellation requested | “Cancellation requested; waiting for runner acknowledgement.” Keep last known execution state and unresolved effects visible; do not claim physical stopping. |
| CANCELLED | “Runner stop acknowledged” or “Cancelled before dispatch,” according to server proof. List any test effects already performed; do not imply rollback. |
| Stream disconnected | Preserve last known state with a stale banner; reconnect does not imply completion. |
| Replay gap / artifact deleted | Explain unavailable history and verification limits. Never replace it with a clean empty timeline. |

The UI renders server-owned values; it cannot derive PASS from receipt presence, a progress percentage or an LLM summary.

## 6. Repair, comparison and human review

The repair screen answers four questions in order: observed failure; exact proposed code change; permitted isolated build effect; verification evidence. Protected-file modification is a hard blocked state, not an ignorable warning. The approval dialog includes base SHA, patch digest, scope `PATCH_APPLY`, expiry and “Does not merge or deploy.” Revocation or a changed revision invalidates the pending action, including in a stale browser tab.

Comparison displays baseline and candidate identity columns, required assertions, task receipt, evidence completeness and functional/security regressions. Highlight unexpected differences; do not visually imply comparability when fixtures, policies or profiles differ. The diff has a keyboard-readable unified text alternative to side-by-side code. A failed candidate retains the reproduced original finding and explains why the repair is not verified.

Review has `ACCEPT`, `CHANGES_REQUESTED`, and `UNABLE_TO_ASSESS`; include journey/profile scope, reviewer role, limitations and notes. Machine outcome remains read-only. Do not require demographic or disability disclosure. An accepted review cannot make an inconclusive run pass. Publication to GitHub is a later separately authorized action and clearly shows public/private repository visibility and the proposed payload.

## 7. Complete operational states

Every route must implement: loading without fake data, first-use empty, populated, partial evidence, validation error, dependency unavailable, stale revision conflict, permission denied, expired session, disconnected stream and recoverable failure. Deep links to deleted or inaccessible resources must not disclose cross-workspace existence. Settings show inherited versus overridden values and whether a change affects queued work.

Usage displays actual measured units and remaining configured entitlement, not invented currency savings. Scheduling shows timezone, next planned run, expiry of standing authorization and missed-run policy. Deletion shows exact objects and retention constraints, asks for scoped confirmation and reports completion separately from request acceptance. Exports remain private by default and reveal expiry before download.

## 8. Accessibility and quality acceptance

- Adopt WCAG 2.2 AA as an engineering target; do not market certification. Measure normal text contrast of at least 4.5:1 and applicable nontext contrast, support 200% zoom and 320 CSS-pixel reflow, visible unobscured focus, semantic headings/landmarks and skip navigation.
- Complete project onboarding, journey correction, replay inspection and human review with keyboard only and actual supported VoiceOver/NVDA profiles. Record exact versions and failures. Include specialist/disabled-user feedback with separate consent before broad claims.
- Avoid drag-only controls, hover-only details, auto-playing media, forced time-limited interaction, inaccessible authentication and color-only status. Dialogs restore focus predictably and do not trap it after unmount.
- Use one restrained live-region strategy; failed forms receive specific announced errors. Keep essential error text persistent; a disappearing toast is not the only recovery instruction.
- Validate responsive layouts at 320/768/1024/1440 CSS pixels, long filenames, long translations, dark mode, reduced motion and browser text scaling. Narrow screens preserve evidence and approvals rather than hiding them.
- Establish measured UI budgets in implementation: useful loading feedback within 1 second, prompt input response and route-split heavy replay/diff views. Load testing must report dataset size, hardware, network and measured percentiles; these targets are not achieved results.

See [TEST-PLAN.md](TEST-PLAN.md) for executable acceptance scenarios and [prompts/21-ui-foundation.md](prompts/21-ui-foundation.md) through [prompts/24-ui-repair-and-review.md](prompts/24-ui-repair-and-review.md) for implementation sequence.
