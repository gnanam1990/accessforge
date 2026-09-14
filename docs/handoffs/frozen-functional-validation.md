# Frozen functional validation contract

`PROTECTED_REFERENCE_VALIDATION` is a backend authoring rule for
`FUNCTIONAL_VALIDATION` only. Its required `suiteDigest` identifies the protected
reference fixture, valid inputs, four invalid-input cases, and their expected HTTP
422 and zero persisted-request outcomes. Callers cannot supply alternative cases
or thresholds. Existing rule canonical forms are unchanged.

The reference regression driver consumes the same immutable input tuples and
includes the suite digest in its policy identity. This intentionally changes the
policy digest; historical receipts must not be upgraded to the new policy.

This change does **not** produce an assertion outcome. Until a trusted producer
authors and retains an outcome bound to the original run, assertion set, suite,
build and lease, the evaluator must continue to treat it as missing evidence.
Check names alone are not observer-authored conditions, and these HTTP/database
checks are not screen-reader evidence. No UI editor, patch-acceptance gate, actual
assistive-technology acceptance or production deployment is added here.

Focused validation: frozen functional rules, existing evaluation rules and
journey DSL tests. Required CI separately covers the changed worker and policy.
