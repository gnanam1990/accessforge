# Prompt 21 — Accessible modern application shell and design system

**Dependencies:** 18  
**Requirements:** FR-014, FR-016, FR-019; INV-07, INV-12  
**Owns:** React application shell, semantic components, tokens, route/session infrastructure and UI test harness

Read [SESSION-HEADER.md](SESSION-HEADER.md), [UI-UX.md](../UI-UX.md), [CONTRACTS.md](../CONTRACTS.md) and UI acceptance criteria in [TEST-PLAN.md](../TEST-PLAN.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 21 only.

Objective: Establish a genuinely usable product foundation with modern visual craft and accessible interaction. This is an engineering review workspace, not a marketing dashboard with fabricated metrics.

Inspect actual package.json, routing, session integration and generated API client. The specification proposes React/TypeScript; if the selected checkout uses another supported stack, record and resolve that architectural difference before importing a new framework. Read the approved UI-UX design system before choosing colors or components.

Implementation tasks:
1. Implement the /w/:workspaceId route map from UI-UX.md, authenticated navigation, workspace/project context and breadcrumbs. Reflect real membership from the API; switching workspace clears previous tenant data and subscriptions safely. Never put fixture values or secret references in navigation URLs.
2. Create semantic typography, spacing, color, elevation and motion tokens from UI-UX.md: Fira Sans/Fira Code with licensed local assets or fallbacks, restrained blue light/dark palettes and measured contrast. Avoid decorative animated charts and ambiguous icon-only controls.
3. Build tested primitives for buttons, links, text fields, selection, status labels, dialogs, tabs, tables, empty states and inline problems. Prefer native semantics; document any custom keyboard pattern.
4. Implement visible focus, skip links, predictable route focus restoration and dialog return focus. Controls remain reachable at required responsive widths and browser zoom without clipping essential actions.
5. Provide form validation with a focusable linked error summary after failed submission plus associated inline field errors. Do not replace labels with placeholders, move focus on every blur or announce repeated duplicate errors.
6. Keep loading, empty, forbidden, expired-session, dependency-unavailable, offline and stale-data states distinct. A skeleton is not proof that data exists; a failed request must not become an empty result.
7. Implement safe API integration and request cancellation. Do not retry mutating approvals implicitly, show cached tenant data after logout or interpret 202 as completed work.
8. Add theme support and reduced-motion behavior according to the approved design. Convey status with text and shape as well as color; preserve contrast in all interactive states.
9. Supply accessible notifications with bounded announcements and dismissibility. Progress changes must not steal focus or overwhelm a screen-reader user with every background event.
10. Establish component, keyboard-flow, responsive and actual AT smoke-test harnesses. Static screenshots and automated accessibility scans are complementary checks, not sufficient acceptance proof.

Required verification:
Test unauthenticated routing, membership revocation, rapid workspace switching, a stale response arriving after logout, denied data, API timeout, keyboard-only dialog operation, failed form focus and reduced motion. Inspect light/dark and narrow/desktop layouts at the sizes specified in UI-UX.md, including zoom. Use the real API for the shell's actual context/session path. Run the navigation/form smoke flow through the supported actual VoiceOver profile and disclose any untested matrix.

Acceptance gate:
A fresh operator can sign in through the implemented mode, choose an authorized workspace, navigate and recover from errors using only the keyboard and supported reader. No placeholder route or static data is presented as a completed feature from later modules.

Handoff:
Write docs/handoffs/21.md with actual UI routes, screenshots as supplementary evidence, keyboard/AT observations, commands/results and remaining integration gaps. Stop after this module.
```
