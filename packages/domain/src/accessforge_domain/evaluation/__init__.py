"""Deterministic outcome evaluation: identity, assertions, the independent observer, the verdict.

Module 02 owns the truth table -- the precedence that turns tri-state conditions into PASS, FAIL or
INCONCLUSIVE. This package owns everything around it: what has to be true before the table is
consulted, where each condition's value is allowed to come from, and what the answer means once it
exists.

Four boundaries run through it, and each is enforced by a type rather than by care:

**Identity before observations.** `revalidate` runs first and its result feeds `EvidenceValidity`,
so a mismatch never reaches the truth table. An evaluator that checked identities afterwards would
compute a verdict and then decide whether to believe it.

**Provenance travels with every value.** The evaluator may derive a reader assertion from a retained
transcript -- that transcript is the reader's own evidence -- and may not derive an application
observation, because no retained artifact implies "the backend received exactly one request".
`assert_provenance_permitted` refuses the second.

**Completion is measured, not read.** The observer counts rows the application actually holds, keyed
on this run's fixture nonce. A page can announce that a request was submitted having submitted
nothing, and every reader assertion in that run would be correctly TRUE.

**A verdict carries its own scope.** `PASS_SCOPE_STATEMENT` is attached to every result rather than
kept in documentation, because a verdict travels into exports and dashboards and the caveat has to
travel with it.
"""

from .assertions import (
    AssertionEvaluation,
    AssertionOutcome,
    Provenance,
    ProvenanceError,
    assert_provenance_permitted,
    evaluate_assertions,
)
from .identity import (
    ALWAYS_REQUIRED,
    IdentityCheck,
    IdentityKind,
    IdentityVerdict,
    revalidate,
)
from .observer import (
    CompletionObservation,
    CompletionObserver,
    EffectCount,
    ObserverError,
    observe_completion,
    observer_writes_nothing,
)
from .verdict import (
    PASS_SCOPE_STATEMENT,
    FindingSupport,
    Verdict,
    decide,
    support_for_finding,
)

__all__ = [
    "ALWAYS_REQUIRED",
    "PASS_SCOPE_STATEMENT",
    "AssertionEvaluation",
    "AssertionOutcome",
    "CompletionObservation",
    "CompletionObserver",
    "EffectCount",
    "FindingSupport",
    "IdentityCheck",
    "IdentityKind",
    "IdentityVerdict",
    "ObserverError",
    "Provenance",
    "ProvenanceError",
    "Verdict",
    "assert_provenance_permitted",
    "decide",
    "evaluate_assertions",
    "observe_completion",
    "observer_writes_nothing",
    "revalidate",
    "support_for_finding",
]
