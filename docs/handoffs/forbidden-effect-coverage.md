# Forbidden-effect coverage contract (C2, partial)

`accessforge_domain.effect_monitor` defines immutable, bounded execution windows and continuously
measured intervals. Run, attempt, scope digest, effect and independent monotonic clock epoch must
all match the trusted expected window. Half-open intervals cannot overlap, be reordered, be
duplicated or escape that window. Complete coverage with zero occurrences gives TRUE (absence);
an observed occurrence gives FALSE even with other gaps. Missing coverage, either boundary,
collector availability or identity match gives UNKNOWN. A final application count is not an input.

This is an internal measurement contract, not an enabled authoring capability or authenticated
producer. Callers must not construct intervals from polling row counts, candidate stdout, or
navigator declarations. A real independent collector must observe the protected sink/enforcement
boundary continuously, including transient effects. The scope digest is a content binding, not
proof that every destination was covered or that the collector was isolated.

Fifteen focused synthetic checks passed, as did Ruff and strict mypy for source and tests. They
cover complete absence, positive occurrence, gaps, late starts/early stops, unavailable collectors,
identity/clock-epoch mismatches, overlaps/replay and invalid numeric values. No real external
effect monitoring, physical reader action, model call or finalizer acceptance is claimed.

## Next required connections

1. Freeze a precise effect/scope/window policy in the protected journey, without accepting prose
   as an executable expectation or granting any external effect authority.
2. Implement the independent collector over an actual owned sink/enforcement boundary; retain
   authenticated original measurements and closure. Uncovered destinations remain UNKNOWN.
3. Bind that source to the sealed execution in persistence and consume only authenticated
   observer-authored results in finalization. Required unsupported assertions remain UNKNOWN now.
4. Exercise positive effects, complete absence, collector interruption and missing closure through
   that integrated path. Do not claim C2 done from these contract tests.
