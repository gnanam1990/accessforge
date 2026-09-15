# Frozen independent effect absence predicate (C2, partial)

Protected assertion contracts can now represent `CONTINUOUS_EFFECT_ABSENCE` with one exact
supported effect and a scope digest. The canonical assertion-set digest covers both. The rule
belongs only to FORBIDDEN_EFFECT / EFFECT_MONITOR; completion counts, reader phrases and
prose-only legacy assertions cannot substitute. Existing rule serialization remains unchanged.

`effect_monitor_assertions` composes the frozen rule with an independently supplied trusted
execution window and measured coverage. A mismatch remains UNKNOWN even if the other scope
observed an occurrence. A matching occurrence is FALSE; complete zero-occurrence coverage is
TRUE. No effects are authorized, expected scopes inferred, or event references manufactured.

51 focused synthetic rule/coverage checks pass; Ruff and strict mypy pass. No real collector,
authenticated retention or finalizer integration is claimed. The rule is deliberately not added
to the advertised runtime authoring-capability list until those pieces exist. Persisted required
assertions without an admitted independent monitor source still finalize UNKNOWN/INCONCLUSIVE.

Next: implement an independent owned-sink/enforcement collector and authenticated source admission,
then wire finalization to that identity. Preserve exact run/attempt/clock/scope binding and full
execution coverage, including effects that disappear before final application-state sampling.
