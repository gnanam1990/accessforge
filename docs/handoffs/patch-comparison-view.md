# Browser original-source comparison

The existing repair page reads the retained comparison API without dispatching preparation,
source scripts, models, builds or approvals. Missing, unavailable and retired source remain
distinct from an empty original file. Explicit readback replaces the comparison; no polling or
automatic mutation occurs. A retirement timestamp suppresses source even if stale payload is present.

The parser checks workspace, exact patch/digest/base, ordered proposed paths and after-text,
byte counts, supported file modes and file-change semantics. Hash fields are server-provided
identities, not independently recomputed browser attestations. The displayed prepared revision
is historical, not the authority of the current approval. Approval still requires its existing
exact-current-revision preview and unchecked acknowledgement.

Native labelled file selection and read-only text controls provide a unified diff and plain
original/proposed alternatives in logical keyboard order. Escaped representations preserve line
endings and expose directional controls. Source renders only as text, never HTML. Replacement-text
fallback remains available, but does not fabricate a missing original source or verified repair.

Local validation is TypeScript/production build plus diff check. Synthetic CI-only fixtures cover
source alternatives, inert hostile text, base mismatch, tombstone suppression and read-only refresh;
they were not locally run. No browser or real assistive-technology acceptance is claimed. Owner
retirement is available through the authenticated API; a browser retirement control remains later work.
