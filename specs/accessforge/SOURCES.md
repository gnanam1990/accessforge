# AccessForge — Source register and evidence limits

Research/constraint snapshot: **9 September 2026**. This file separates official facts, public problem signals, competitor claims and product hypotheses. It is not proof that AccessForge has customers, passed runtime tests or qualifies for a prize.

## 1. Event constraints

The supplied [AWS Developers announcement](https://x.com/awsdevelopers/status/2097363081131098205?s=20) led to **Agents for Humans**. Use the [official rules](https://agentsforhumans.devpost.com/rules) for submission requirements and the [FAQ](https://agentsforhumans.devpost.com/details/faqs) for operational clarification; refresh both before publishing/submitting.

| Item | Verified event fact or our interpretation |
|---|---|
| Submission deadline | 14 September 2026, 17:00 PDT = 15 September, 00:00 UTC = **15 September, 05:30 IST**. |
| Judging end / access horizon | 8 October, 17:00 PDT = 9 October, 00:00 UTC / 05:30 IST. Preserve judge access to the submitted working build. |
| Required technology | A new working agent using Strands Agents SDK. AgentCore is optional, not a prerequisite. |
| Submission package | Public MIT/Apache repository, README, architecture diagram, English materials, public video of at most five minutes, Builder ID and working-project access. |
| Existing work | Disclose incorporated pre-existing work; do not claim an older implementation was newly built in the event period. |
| Track recommendation | **Professional Agents**, because the initial product serves engineering/accessibility work. This is our fit judgment, not organizer approval. |
| Awards | One prize per project; no guaranteed award or assumed stacking. |
| Credits | The FAQ/rules give a request cutoff of 11 September, noon PDT; availability is limited. This is not evidence of this user's approval or balance. |

The full product is intentionally larger than the remaining event window. E0 is the submission-shaped working slice; R1 is the broader release. Never remove a safety or evidence gate merely to meet a calendar date. [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) and [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) own readiness, not a promise of completion by the deadline.

## 2. Problem and competition evidence

| Source | Evidence supported | Strength / limitation |
|---|---|---|
| [WebAIM Million](https://webaim.org/projects/million/) | Large-scale homepage measurements find widespread detectable accessibility errors and explain limits of automated detection. | Strong evidence for prevalence in its measured population; not proof of all journey failures or willingness to buy this product. |
| [Accessibility discussion: surveys](https://www.reddit.com/r/accessibility/comments/1sfzsnu/challenges_using_and_making_accessible_surveys/) | A participant describes practical accessibility barriers in surveys/forms. | Individual public self-report, useful discovery lead; not representative incidence, verified customer interview or purchase intent. |
| [Guidepup issue #99](https://github.com/guidepup/guidepup/issues/99) | Practitioner interest in screen-reader automation and integration with QA workflows. | Closed issue. Do not describe it as an unresolved production bug or infer a budget from it. |
| [Deque axe MCP server](https://docs.deque.com/devtools-server/4.0.0/en/axe-mcp-server/) | Existing accessibility tooling integrates analysis/remediation with AI-assisted developer workflows. | Primary product documentation; strong competitor evidence, not an independent quality benchmark. |
| [Evinced AI monitoring](https://www.evinced.com/ai/monitoring) | Vendor describes AI-driven journey/screen-reader monitoring direction. | Competitive overlap is real; distinguish experimental/labs material from proven generally available capability. |

Public X/Reddit/GitHub and primary documentation informed the preceding idea research. Medium material reviewed was mainly commentary/vendor framing, not strong independent buyer evidence. This is bounded coverage, not a claim to have exhaustively read every platform or private conversation. No unverified procurement snippet, issue-template budget dropdown or anecdotal savings figure is treated as willingness to pay.

**Inference:** the candidate opportunity is accountable handoff from a reproduced barrier to a constrained repair and independently reviewable regression evidence. “AI accessibility testing” alone is not novel. Competitors may already cover parts of this workflow; validate the exact gap with live product trials and prospective users before making differentiation claims.

## 3. Technical primary sources

| Reference | Why it matters | What still needs local proof |
|---|---|---|
| [Guidepup repository](https://github.com/guidepup/guidepup) | Existing actual VoiceOver/NVDA automation building block. | Exact OS/browser/reader permissions, capture, focus, reset and reliability on our dedicated runner. Virtual adapters are not actual-AT proof. |
| [Strands Python quickstart](https://strandsagents.com/docs/user-guide/quickstart/python/) | Real SDK setup and agent execution. | Compatible pinned dependencies, provider access, real tool call and reproducible installation. |
| [Strands tools](https://strandsagents.com/docs/user-guide/concepts/tools/) | Tool integration and executable tool-loading behavior. | Explicit trusted registry; no untrusted repository tool autodiscovery or authority escalation. |
| [Strands interrupts](https://strandsagents.com/docs/user-guide/concepts/interrupts/) | SDK pause/interaction mechanism. | Durable application approvals, revocation, exact target binding and restart semantics. |
| [AgentCore policy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html) | Policy for actions passing through configured gateway controls. | No direct-path bypass. Optional adoption must not be represented as host-wide enforcement. |
| [GitHub webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries) | Raw-payload signature verification. | Replay dedupe, tenant/installation scope, fork isolation and explicit publication authority. |
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | Normative reference for the UI engineering target. | Actual conformance evaluation; automated checks and this specification do not certify compliance. |

The stack, repository paths, API routes, schemas, invariants and benchmarks in this package are **our proposed design**, not copied vendor guarantees. Verify APIs against selected versions during implementation; example AccessForge action names are not assumed Guidepup SDK method names.

## 4. Buyer and feasibility validation gates

Before positioning this as a standalone business, observe three relevant operators, reproduce two real barriers with actual AT (including at least one authorized externally reported incident), and obtain consented reviewer feedback on a repair. A design-partner agreement can establish access and collaboration, not willingness to pay. The separate commercial gate requires an actual paid pilot or explicit documented commercial commitment. These are proposed validation gates—not completed interviews or invented demand.

Compare with the user's existing paid tools: which exact work remains manual, how often, who owns the budget, what source/environment access is permitted, and why their current workflow cannot accomplish the same result at acceptable cost. Record refusals and contradictory evidence. A grant, hackathon prize, trial signup or positive comment is not recurring product revenue.

Kill or narrow the thesis if real-AT operation is unreliable, isolation prevents useful integration, diagnosis/repair needs prohibitive manual labor, existing tools already satisfy the buyer, or operators will not provide an authorized environment/pay for the outcome. A useful open-source integration or internal tool remains a valid outcome; a large company narrative is not required.
