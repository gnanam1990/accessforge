# Retained diagnosis delivery — implementation in progress

The missing core path is retained run evidence → diagnosis → immutable finding occurrence →
reviewer-visible explanation/repair brief. This branch starts that connection; it is not yet a
completed diagnosis module, deployed model integration or physical-reader acceptance result.

`diagnosis.projection.prepare` now loads the original completed FAIL/INCONCLUSIVE evaluation and
its exact attempt. It reuses the verifier's five-artifact readback/integrity checks and compares the
artifact identities with the original evaluation snapshot. Protected descriptions come from the
frozen assertion contract, and conditions come from the original verifier snapshot, not caller
JSON. Reader observations carry bounded recorded text only when capture is known and unredacted;
unknown/redacted observations remain explicitly marked without fabricated announcements.

The privately provisioned authorized source scope is measured before and after narrow excerpt
reads. The clean commit and tree must match the sealed manifest, and the existing frozen reader
checks each file digest/path/line range and its budget. The result binds the evaluation, manifest
and complete projection digests, without granting execution or changing run/finding status.

This is a trusted internal assembly function, not a public unauthenticated API. The host must own
the source scope and workspace transaction. No model was invoked, no finding was created, and no
actual source intake or reader was run while implementing it. Changed-file Ruff/mypy checks pass;
scoped synthetic assembly regressions are authored for CI, not locally executed. Real retained
artifact/source integration coverage remains part of completing the delivery path.

Next: immutable diagnosis revisions and conservative behavior/component grouping that preserves
each run/source occurrence; fresh authorization and input rechecks around model execution;
finding admission derived from verified prerequisites; reviewer-readable original hypothesis,
alternatives, uncertainty and scoped repair brief. Existing human feedback remains separately
attributable and must not mutate original diagnosis or machine outcomes.
