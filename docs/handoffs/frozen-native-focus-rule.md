# Frozen native keyboard-focus predicate

`EXACT_NATIVE_KEYBOARD_FOCUS` freezes an action sequence, a supported AX role and the
native identifier SHA-256 digest. It is permitted only for `FOCUS_BEHAVIOUR`. Its
canonical fields participate in the existing assertion digest; the action must leave
one budget slot for STOP. Expected identity remains outside navigator policy.

The finalizer projects private focus metadata from the original verified retained
reader-observation artifact. It joins the original supervisor intent and successful
non-STOP result by action ID and sequence, requiring canonical intent < capture < result.
Only one known native capture can yield TRUE or FALSE by exact role/digest comparison.
Missing, explicit UNKNOWN, duplicate, unmatched, failed or ambiguous action evidence
stays UNKNOWN. Invalid source shape or provenance is refused. Speech and its redaction
do not determine native focus; a native observation is not a phrase-derived selector.

The historical projection requires prior artifact and canonical-source verification.
Server admission still validates the timestamp against its original unresolved action
and freshness window. Shape-only historical validation cannot admit new records.
Evaluator version is now 1.11.0; this does not rewrite historical evaluations or infer
predicates for old prose-only assertions.

Evidence boundary: scoped synthetic tests and static checks establish implementation
behavior, not actual Safari/VoiceOver acceptance. An exact identifier hash is neither
anonymization nor proof of identifier uniqueness/stability. It measures AX keyboard
focus, not the VoiceOver cursor. Actual supported-profile qualification and suitable
fixture identity must be established separately. Full-run preflight, runtime identity,
observer and retention gates are unchanged; this predicate alone cannot establish PASS.

The API advertises the closed contract. The journey editor now offers an explicit
native-focus assertion only for a compatible advertised capability. Its labelled action,
role and identifier-digest fields have linked inline/summary errors and preserve entered
values. The role and digest are not guessed or derived from reader text. Changed or
missing capabilities prevent freezing; an existing rule can still be disabled.
Adding focuses the description and removing returns focus to the add button.

Targeted web checks cover payload serialization, invalid digest/role/budget/capability,
form errors and add/remove focus. Type-check and production build also passed locally.
These DOM-based checks are not browser visual or physical screen-reader qualification.
Actual native capture through retained storage into the finalizer remains separate
acceptance work. No screen reader was started or permission changed for this implementation.
