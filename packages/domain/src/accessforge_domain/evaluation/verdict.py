"""Assembling a final verdict, and stating exactly what it establishes.

Module 02 owns the truth table. This module gathers the inputs to it -- identity, completeness,
assertion values, the completion observation -- applies it, and attaches the scope statement without
which a PASS is routinely over-read.

`FindingSupport` is here rather than in a findings module because the rule it encodes is an
evaluation rule: **only a complete, valid, failed run can support REPRODUCED.** A partial run may
suggest a CANDIDATE. The distinction exists because "we observed a failure" and "we established a
failure" are different claims, and only the second is a defect report someone should act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from accessforge_domain.outcome import EvaluationInput, EvidenceValidity, evaluate
from accessforge_domain.states import Condition, FindingStatus, Outcome

from .assertions import AssertionEvaluation
from .identity import IdentityVerdict
from .observer import CompletionObservation

#: What a PASS on one journey and one profile does and does not establish.
#:
#: Attached to every verdict rather than kept in documentation, because a verdict travels -- into an
#: export, a dashboard, a slide -- and the caveat has to travel with it. A PASS separated from this
#: sentence is routinely read as "the product is accessible", which it is not and cannot be.
PASS_SCOPE_STATEMENT = (
    # noqa below: the linter sees "PASS" in the name and guesses credential. It is the opposite --
    # this is the sentence that stops a PASS being over-read.
    "This result describes one journey, executed once, on one pinned reader, browser, locale and "  # noqa: S105
    "keyboard layout, against one build of one environment. It establishes that the frozen "
    "assertions held for that execution. It does NOT establish that the application is usable by "
    "people with disabilities in general, that it works with other assistive technologies or other "
    "versions of this one, that untested journeys behave the same way, or that any legal or "
    "regulatory obligation is satisfied. Actual screen-reader automation supplements evaluation by "
    "disabled users and specialists; it does not replace it."
)


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: Outcome
    reasons: tuple[str, ...] = field(default_factory=tuple)
    scope: str = PASS_SCOPE_STATEMENT
    evaluator_version: str = ""
    evidence_set_digest: str = ""


def decide(
    *,
    identity: IdentityVerdict,
    completeness_reasons: tuple[str, ...],
    preflight_passed: bool,
    assertions: AssertionEvaluation,
    completion: CompletionObservation,
    evaluator_version: str,
    evidence_set_digest: str,
) -> Verdict:
    """Produce the final verdict for one attempt.

    Deterministic: the same inputs give the same answer, with no clock, no model and no confidence
    score anywhere in the path. That is what makes an outcome independently recomputable by anyone
    holding the evidence, which is the property the whole export story rests on.

    Identity and completeness are folded into `EvidenceValidity` rather than checked separately,
    so they pass through the same precedence rules as everything else and cannot be accidentally
    evaluated after the observations.
    """
    validity = EvidenceValidity(
        identity_bound=identity.bound,
        preflight_passed=preflight_passed,
        evidence_chain_intact=not completeness_reasons,
        producer_tails_closed=not completeness_reasons,
    )

    result = evaluate(
        EvaluationInput(
            validity=validity,
            required_assertions=assertions.conditions(),
            completion=completion.condition,
        )
    )

    # The truth table says *what*; these say *why*, in the words of whatever actually went wrong.
    # `evaluate` can only report "evidence chain has gaps"; the caller knows which producer.
    detail: list[str] = list(result.reasons)
    if not identity.bound:
        detail.extend(identity.reasons)
    detail.extend(completeness_reasons)
    if result.outcome is Outcome.INCONCLUSIVE:
        detail.extend(assertions.unknown_reasons())
        if completion.condition is Condition.UNKNOWN:
            detail.append(f"completion unknown: {completion.detail}")
    if result.outcome is Outcome.FAIL:
        detail.extend(f"false: {a}" for a in assertions.false_assertions())
        if completion.condition is Condition.FALSE:
            detail.append(f"completion false: {completion.detail}")

    return Verdict(
        outcome=result.outcome,
        reasons=tuple(dict.fromkeys(detail)),
        evaluator_version=evaluator_version,
        evidence_set_digest=evidence_set_digest,
    )


@dataclass(frozen=True, slots=True)
class FindingSupport:
    status: FindingStatus
    rationale: str


def support_for_finding(verdict: Verdict) -> FindingSupport | None:
    """What, if anything, this verdict may support as a finding.

    Only a complete valid FAIL supports REPRODUCED. An INCONCLUSIVE run that happened to contain a
    FALSE observation supports a CANDIDATE and nothing stronger: the observation is real, and the
    evidence around it is not complete enough to say the behaviour was established rather than
    coincident with whatever else went wrong.

    A PASS supports nothing. There is no such thing as a finding that something worked.
    """
    if verdict.outcome is Outcome.FAIL:
        return FindingSupport(
            FindingStatus.REPRODUCED,
            "a complete, valid run observed the frozen assertion as false, which is what "
            "REPRODUCED requires: the behaviour was established rather than merely seen.",
        )
    if verdict.outcome is Outcome.INCONCLUSIVE:
        return FindingSupport(
            FindingStatus.CANDIDATE,
            "the run is inconclusive, so nothing is established. Any failure observed within it is "
            "a candidate for investigation and is not a confirmed defect: incomplete evidence "
            "cannot distinguish a real failure from a consequence of what was missing (INV-02).",
        )
    return None
