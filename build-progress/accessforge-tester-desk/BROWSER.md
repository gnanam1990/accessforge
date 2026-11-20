# AccessForge Tester Desk · Real-browser proof

One command boots the real FastAPI service and the real Vite/React app on
ephemeral loopback ports, provisions a disposable AF-21 scene through the real
HTTP entry points, and drives a normal Chromium through the documented
journey. 40 named checks; non-zero exit on any failure; a redacted evidence
record (JSON + screenshots + Tab-path audit) lands in a timestamped directory.

## Runnable command

```sh
cd tools/tester-desk-browser
npm install --cache .npm-cache       # playwright-core only, no browser download
node check.mjs
```

Sandboxed shells (this devin sandbox included) need two extra browser flags:

```sh
AF_BROWSER_ARGS="--single-process --no-sandbox" node check.mjs
```

## Dependencies

- Python env: `uv sync` in the repo root (already required for the API).
- Node: any modern release (`node --check` passes on v22); `playwright-core`
  is pinned in `tools/tester-desk-browser/package.json` and drives an explicit
  executable — no `npx playwright install` step.
- A Chromium/Chrome binary. `AF_BROWSER_BIN` overrides the default lookup:
  the macOS Playwright cache `chromium_headless_shell-1254` first, then
  `chrome-headless-shell` on PATH. Any Chrome-for-Testing / Chromium works.
- Postgres reachable at `AF_DATABASE_URL`
  (default `postgresql://af_td:af_td_local@localhost:5432/af_td_dev`).
  The check applies any pending migrations itself before the API starts —
  a fresh database is valid input.

## Configuration (all env vars)

| Var | Default | Purpose |
|---|---|---|
| `AF_BROWSER_BIN` | Playwright cache shell / PATH | browser executable |
| `AF_BROWSER_ARGS` | — | extra launch flags (`--single-process --no-sandbox` under sandboxing) |
| `AF_DATABASE_URL` | `af_td_dev` DSN | disposable scene + migrations |
| `AF_API_PORT` / `AF_WEB_PORT` | free port chosen | pin service ports |
| `AF_EVIDENCE_DIR` | `evidence/<timestamp>` | where artifacts land |
| `AF_KEEP_PROCESSES` | unset | leave API+vite running, print URLs |

## What the check proves (40 checks, all through the real UI)

- TESTER signs in via the form, workspace choice lands on the tester inbox
  (not overview); CLIENT lands on the report list; ordinary member/reviewer
  still lands on overview.
- Offer card renders; claim stays disabled until consent; the pinned consent
  terms are readable before accepting; checkbox ticked with Space, claim
  activated with Enter.
- Capture screen: "Not yet submittable" warning on an empty draft, a visible
  problem notice on premature submit, per-step results and notes set by
  keyboard, `ACTION_TRACE` evidence attached through the UI.
- Draft persists across a full page reload (result + unreachable note).
- 320px viewport: no document-level horizontal overflow (wide tables clip
  inside their scroll regions), Tab reaches radios, file input, Save and
  Submit; the focus path is recorded to `tab-paths.json`.
- Submit seals: FAILED task result + AWAITING REVIEW badge both visible.
- Reviewer accepts the digest-bound evidence via the queue; the card clears;
  exactly one payable exists in INTENT (verified over the owner session, since
  REVIEWER cannot read finance); the tester sees ACCEPTED + the reviewer
  justification + PENDING DISPATCH on their own surface.
- Client report generated through the API (COMMISSION_AUTHOR); the client
  sees the rendered findings and none of: tester identity, payout route,
  access credentials.
- Outsider and tester on direct agency-console URLs see refusals, no data.

## Evidence

`tools/tester-desk-browser/evidence/<timestamp>/` per run:

- `evidence.json` — scene ids, redacted emails (`td-browser-…@…` truncated),
  every check's name/result/detail, screenshot list.
- `tab-paths.json` — the recorded Tab order at desktop and 320px.
- `NN-*.png` — full-page screenshots at each milestone, including
  `07-narrow-320px.png`, `10b-tester-decision.png`, `14-tester-agency-refused.png`.

All identities are synthetic per-run (`td-browser-*-<runId>`); the database
DSN is redacted in the record. Evidence directories are git-ignored.

Latest in-sandbox run: **40/40 passed** (evidence `2026-11-20T08-47-39`).

## Product gaps found and fixed by this proof

- Non-member testers/client viewers could not reach their surfaces at all —
  session workspace list only read `workspace_membership` (fixed: session
  union + SELECT-only own-row RLS policies, migration `0081`).
- Workspace index routed TESTER/CLIENT to the member overview (fixed:
  role-aware landing + filtered primary navigation).
- Offer list omitted the consent body a tester must attest to (fixed: terms
  pinned onto each offer item).
- Capture screen had no evidence-attachment control, so a rubric requiring
  `ACTION_TRACE` could never be satisfied through the UI (fixed: upload
  section + manifest table).
- 320px document-level horizontal overflow from grid/flex `min-width:auto`
  (fixed: shrink rules; tables clip inside `.af-table-scroll`).

## Explicitly external (not proven here, by design)

- A consenting human tester running real VoiceOver/Safari — every tester
  identity is synthetic.
- A live payout provider — the payable correctly stays `PENDING DISPATCH`;
  no settlement is claimed.
- The synthetic target URL is a fixture; no third-party site was driven.
