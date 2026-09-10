"""Protected assertion definitions.

Each assertion names the one observer entitled to decide it, and says what makes its observation
*unknown* rather than false. That second part is what keeps INV-02 enforceable: "we could not see
it" and "it was wrong" are different answers, and collapsing them turns a gap in evidence into a
reported defect.

Assertions are protected in the literal sense — the types here are frozen, the set is hashed into
the compiled journey, and nothing in the patch or navigator path can construct a different one and
have it accepted for an existing run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Observer(StrEnum):
    """Who is entitled to decide an assertion.

    Not a hint. The evidence ACL in module 03 enforces which identity may submit which record, and
    these values say which of those records an assertion is allowed to read.
    """

    READER = "READER"
    """The screen reader, via the supervisor. Announcements, focus and reading order."""

    APPLICATION_OBSERVER = "APPLICATION_OBSERVER"
    """The independent observer reading the application's own durable state. Receipts."""

    FUNCTIONAL_TEST = "FUNCTIONAL_TEST"
    """A protected functional or security test, used to show a repair did not remove validation."""

    EFFECT_MONITOR = "EFFECT_MONITOR"
    """Watches for effects that must NOT happen. Its true value is absence."""


class AssertionKind(StrEnum):
    TASK_COMPLETION = "TASK_COMPLETION"
    """The task actually completed, per the application's durable state.

    Decided by the application observer, never by the reader: a phrase announced on screen is not
    evidence that a row was written.
    """

    REQUIRED_ANNOUNCEMENT = "REQUIRED_ANNOUNCEMENT"
    """Something was announced to the reader.

    Decided by the reader only. A backend row existing proves nothing about what was spoken.
    """

    FOCUS_BEHAVIOUR = "FOCUS_BEHAVIOUR"
    """Focus moved where it should. Reader-observed."""

    READING_ORDER = "READING_ORDER"
    """Content was reached in the expected order. Reader-observed."""

    FORBIDDEN_EFFECT = "FORBIDDEN_EFFECT"
    """An effect that must not occur. True means it did not happen."""

    FUNCTIONAL_VALIDATION = "FUNCTIONAL_VALIDATION"
    """Server-side validation still rejects what it should.

    The assertion that makes INV-16 checkable: a repair that loosened validation to make the journey
    pass fails here.
    """


# Which observer decides which kind. One entry per kind, deliberately: a kind decidable by two
# observers would let the weaker one answer for the stronger.
ASSERTION_OBSERVERS: dict[AssertionKind, Observer] = {
    AssertionKind.TASK_COMPLETION: Observer.APPLICATION_OBSERVER,
    AssertionKind.REQUIRED_ANNOUNCEMENT: Observer.READER,
    AssertionKind.FOCUS_BEHAVIOUR: Observer.READER,
    AssertionKind.READING_ORDER: Observer.READER,
    AssertionKind.FORBIDDEN_EFFECT: Observer.EFFECT_MONITOR,
    AssertionKind.FUNCTIONAL_VALIDATION: Observer.FUNCTIONAL_TEST,
}


class UnknownReason(StrEnum):
    """Why an assertion's value may be unknown rather than false.

    Declared per assertion so the evaluator can distinguish a missing observation from a negative
    one without guessing. An assertion with no declared unknown reasons is claiming it can always be
    decided, which is rarely true of anything involving a screen reader.
    """

    READER_UNAVAILABLE = "READER_UNAVAILABLE"
    OBSERVATION_MISSING = "OBSERVATION_MISSING"
    AMBIGUOUS_LANGUAGE = "AMBIGUOUS_LANGUAGE"
    PROFILE_UNSUPPORTED = "PROFILE_UNSUPPORTED"
    OBSERVER_UNREACHABLE = "OBSERVER_UNREACHABLE"


@dataclass(frozen=True, slots=True)
class Assertion:
    """One protected truth condition.

    ``required`` distinguishes a condition that gates the verdict from one recorded for information.
    A required assertion that cannot be observed makes the run INCONCLUSIVE; an optional one does
    not.
    """

    assertion_id: str
    kind: AssertionKind
    description: str
    required: bool = True
    unknown_reasons: frozenset[UnknownReason] = frozenset()

    def __post_init__(self) -> None:
        if not self.assertion_id.strip():
            raise ValueError("an assertion needs a stable identifier")
        if not self.description.strip():
            raise ValueError(
                f"assertion {self.assertion_id} needs a description a reviewer can read"
            )
        if self.required and not self.unknown_reasons:
            raise ValueError(
                f"required assertion {self.assertion_id} declares no way to be unknown; "
                "a condition "
                "that can only be true or false cannot represent a missing observation, which is "
                "how an unobserved assertion becomes a reported defect"
            )

    @property
    def observer(self) -> Observer:
        """The only observer entitled to decide this assertion."""
        return ASSERTION_OBSERVERS[self.kind]


@dataclass(frozen=True, slots=True)
class AssertionSet:
    """An immutable, ordered set of assertions.

    Order is preserved so a reviewer reads them the way they were written, but identity is by
    content: the digest is computed over a canonical form in module-02 canonicalization.
    """

    assertions: tuple[Assertion, ...]

    def __post_init__(self) -> None:
        if not self.assertions:
            raise ValueError("a journey needs at least one assertion")
        ids = [a.assertion_id for a in self.assertions]
        if len(set(ids)) != len(ids):
            raise ValueError("assertion identifiers must be unique within a set")
        if not any(a.kind is AssertionKind.TASK_COMPLETION and a.required for a in self.assertions):
            raise ValueError(
                "a journey must include a required TASK_COMPLETION assertion; required "
                "completion is "
                "a frozen condition, not an optional extra"
            )

    @property
    def required(self) -> tuple[Assertion, ...]:
        return tuple(a for a in self.assertions if a.required)

    def canonical_form(self) -> dict[str, object]:
        return {
            "assertions": [
                {
                    "assertionId": a.assertion_id,
                    "kind": a.kind.value,
                    "description": a.description,
                    "required": a.required,
                    "observer": a.observer.value,
                    "unknownReasons": sorted(r.value for r in a.unknown_reasons),
                }
                for a in self.assertions
            ]
        }
