# Protected functional-validation authoring

The journey editor offers a required functional-validation assertion only when the server
advertises FUNCTIONAL_VALIDATION and a well-formed PROTECTED_REFERENCE_VALIDATION suite digest.
The author explicitly enables the executable rule; the exact server suite is then shown in a
labelled read-only field. It is not typed, inferred from prose or sent to the navigator.

The draft captures the digest when enabled. A changed or missing capability refuses freeze with
a linked field error; a changed suite must be reviewed by turning the rule off and back on.
Unsupported rules can always be turned off, but cannot be newly enabled. Disabled rules serialize
without an evaluationRule, so descriptions alone still do not establish a known outcome.

Adding focuses the new assertion description. Removing a draft functional assertion announces
the change and returns focus to its add button. Existing FormField labels, descriptions and the
linked error-summary pattern are reused; no new visual design or color-only feedback is added.
The suite explanation distinguishes protected invalid-input/no-write evidence from task or reader
completion. Freezing does not execute the suite, approve a run or modify an existing frozen version.

Scoped checks cover exact submitted rule/digest, read-only display, add/remove focus, missing or
malformed capability, changed suite, disabled serialization and unsupported assertion families.
The two scoped test files passed 39 checks; web typecheck and production build passed. Tests use
the synthetic API server and jsdom, not a real browser, backend freeze or actual screen reader.
Required CI, rendered browser checks and physical accessibility acceptance remain separate.
