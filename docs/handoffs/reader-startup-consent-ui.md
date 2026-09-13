# Reader startup consent — operator UI

Implemented on `feat/reader-startup-consent-ui`, stacked on the consent storage/API/native work
in PR38. The run screen now offers an explicitly opened consent section, with no background
polling or machine-secret handling in the browser.

- Inspect original consent history, exact desktop/runner/operator/expiry and session binding.
- Owners explicitly select a runner and fetch the current approved scope. A changed run revision
  or manifest cannot be used for issuance; the server independently checks current authority.
- Display the pinned SDK startup effects, excluded OS permissions, exact identity digests and UTC
  expiry. A dedicated-desktop acknowledgement is initially unchecked and resets on scope/expiry edits.
- Store one explicit decision with reviewed If-Match and a per-review idempotency key. A stored
  decision is not a reader launch, physical readiness or a fresh execution authorization.
- Confirm permanent exact-consent revocation in the existing keyboard-oriented dialog. No claim
  that already-entered SDK work stopped, and no restore/reissue button.
- Invalid expiry has a linked inline error and focus; request outcomes are announced. Unmount
  aborts observation and discards late replies. Read stored consent again to reconcile an unknown write.

UI/UX guidance shaped explicit confirmation, form submission feedback, native labelled controls,
inline errors and token-based styling. Existing theme/spacing/focus conventions are preserved.
Web TypeScript/Vite production build and diff checks passed. Four focused synthetic UI/parser cases
are authored for CI only. No local test suite, browser/reader loop, actual consent or OS action was run.

Still pending: this branch's GitHub CI/merge, deployed operator-to-native provisioning and actual
physical reader/runtime proof. PR38 review findings must be corrected before that parent is merged.
