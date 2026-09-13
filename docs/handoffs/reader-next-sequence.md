# Frozen consecutive-NEXT reading-order rule

Journey API authoring now accepts `READER_NEXT_SEQUENCE` for `READING_ORDER` assertions. A rule
contains 2–20 consecutive action ordinals and exact expected reader phrases. Its last ordinal must
fit the frozen action budget and the action policy must allow NEXT. No regex, selector, inferred
wording or mutable expectation list is accepted. Phrase limits are exposed by journey-capabilities.
The rule is included in the assertion/journey digests and reviewer contract, not navigator policy.

Example evaluationRule:

```json
{
  "type": "READER_NEXT_SEQUENCE",
  "steps": [
    {"actionSequence": 2, "phrase": "Name, edit text"},
    {"actionSequence": 3, "phrase": "Email, edit text"}
  ]
}
```

The finalizer joins retained supervisor ACTION_TRACE and SPEECH_TRANSCRIPT records after original
artifact/stream verification. Each declared action must be NEXT with a retained SUCCEEDED result;
its single reader capture must be canonically between intent and result and match the action ID and
ordinal. Captures across the sequence must be ordered, uniquely referenced and unredacted. Complete
literal matches yield TRUE; complete differing phrases yield FALSE. Missing, conflicting, failed,
wrong-action or unknown/redacted capture yields UNKNOWN, even if another phrase differs.

Values are EVALUATOR_DERIVED with the exact canonical speech-event references, never observer-authored
or independent physical attestation. This checks one frozen consecutive NEXT traversal; it does not
prove DOM/tab order, focus coordinates, arbitrary reading strategies or cross-reader transcript parity.
It does not implement the separate FOCUS_BEHAVIOUR, FORBIDDEN_EFFECT or FUNCTIONAL_VALIDATION families.

Evaluator version is 1.2.0. Original persisted evaluations remain unchanged; a new finalization against
an older sealed evaluator version does not bypass identity mismatch. Runtime/profile/source/model
admission and actual reader acceptance are still required for an end-to-end verdict.

Local validation is scoped Ruff/mypy and diff checks only. Existing CI receives bounded synthetic
stream/rule cases and expanded real-PostgreSQL API authoring coverage. No local pytest, OS action,
reader startup, provider call or deployment occurred. Dedicated visual authoring controls remain a
separate UI continuation; the rule can be authored through the existing authenticated journey API.
