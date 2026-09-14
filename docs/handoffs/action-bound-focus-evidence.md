# Action-bound private focus evidence admission

The existing authenticated observation endpoint accepts an optional, closed `keyboardFocus`
record within its original reader source. The original session/lease/manifest/action gates,
unresolved dispatched action, source digest, redaction, canonical sequencing and replay checks
remain mandatory. No new public caller-selected run or lease identity is added.

Focus metadata contains AX_KEYBOARD_FOCUS, KNOWN/UNKNOWN and a capture timestamp. Known metadata
contains only an allowlisted role and 64-character identifier digest; unknown metadata contains
only NATIVE_FOCUS_UNAVAILABLE. Arbitrary diagnostics, input values, selectors and verdict fields
are refused. Capture must fall between original dispatch and the server clock, at most 30 seconds
old. Source claims are not independently attested native measurements.

The private native session method accepts the optional record separately from the ordinary reader
observation. It validates and constructs the closed source before hashing/sending. Existing callers
remain unchanged. This step does not yet invoke the focus collector from physical action execution.
The typed bootstrap forwards the optional record without granting new action authority.

The navigator reconstruction verifies any focus metadata against original dispatch/result timing,
then constructs only the existing reader-text fields. Focus role/digest never enters its model
input. Missing focus does not borrow a spoken phrase or synthesize a target. No focus predicate or
evaluation outcome is introduced here; physical producer composition and qualification remain next.

Verification: 18 focused Python validation/projection checks, one TypeScript envelope check,
TypeScript build and full strict mypy passed. Eight API/PostgreSQL admission cases passed using a
fresh disposable database; the successful case retains synthetic focus metadata, rejects stale or
private-field substitutions, verifies redaction/digests, and preserves replay/terminal refusal.
The disposable database was dropped. No live schema change, AT startup or physical proof occurred.
