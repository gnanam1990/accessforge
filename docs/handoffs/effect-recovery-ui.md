# Read-only form transport history in the Run screen

The Run screen reads the original `effect-deliveries` endpoint with evidence-read permission.
It shows transport phase, recorded action result, response status, investigation flag, lease/STOP
timestamps, run and runner quarantine, and endpoint cleanup separately. Missing cleanup stays
unknown. A retained response is not application success, an independent effect receipt, or a verdict.

Reads use explicit refresh and 20-record pages ordered by permission UUID, not chronology. No polling,
automatic page draining, resend, reset, new dispatch, lease release or background reconciliation occurs.
Each page has its own observation time; restarting reads page one again rather than mixing snapshots.
The display parser rejects foreign run IDs, malformed phase/response pairs, repeated/out-of-order
permission IDs, invalid cursors and any claimed retry/reset authority. Navigation aborts the old read;
actor, workspace, run and role changes replace the mounted history. API authorization remains decisive.

Existing semantic tokens, native buttons, textual statuses and expandable identity details are reused.
Long identifiers wrap; page navigation moves focus to the new page heading. No reader was started and
no OS permission, database state, provider account or production deployment was changed for this UI.

Validation: web production build/typecheck and diff checks locally. Focused synthetic parser, paging,
read-error, no-mutation and stale-response cases are authored for existing CI only; no local test suite
or physical assistive-technology run was performed. Live browser/reader acceptance remains separate.
