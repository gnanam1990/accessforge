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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


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
#
# Read through a MappingProxyType rather than exported as a plain dict. `Assertion.observer` looks
# the value up at read time rather than storing it, so a caller that reassigned an entry here would
# change which identity is authoritative for an assertion *without changing that assertion's
# digest* -- a sealed journey would keep its digest and silently start accepting the navigator's own
# word for what the screen reader announced. INV-05 says agents cannot edit the evaluator; a module
# -level dict is an edit away from exactly that, and the proxy makes the assignment raise.
ASSERTION_OBSERVERS: Mapping[AssertionKind, Observer] = MappingProxyType(
    {
        AssertionKind.TASK_COMPLETION: Observer.APPLICATION_OBSERVER,
        AssertionKind.REQUIRED_ANNOUNCEMENT: Observer.READER,
        AssertionKind.FOCUS_BEHAVIOUR: Observer.READER,
        AssertionKind.READING_ORDER: Observer.READER,
        AssertionKind.FORBIDDEN_EFFECT: Observer.EFFECT_MONITOR,
        AssertionKind.FUNCTIONAL_VALIDATION: Observer.FUNCTIONAL_TEST,
    }
)


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


MAX_RULE_ACTION_SEQUENCE = 1000
MAX_RULE_PHRASE_CHARACTERS = 8192
MAX_RULE_PHRASE_BYTES = 32768
MAX_RULE_EFFECT_COUNT = 1000
MAX_READER_SEQUENCE_STEPS = 20


def evaluation_rule_capabilities() -> dict[str, Any]:
    """Authoring limits from the same constants used by frozen rule validation."""
    return {
        "EXACT_READER_PHRASE": {
            "assertionKind": "REQUIRED_ANNOUNCEMENT",
            "maxActionSequence": MAX_RULE_ACTION_SEQUENCE,
            "maxPhraseCharacters": MAX_RULE_PHRASE_CHARACTERS,
            "maxPhraseBytes": MAX_RULE_PHRASE_BYTES,
        },
        "EFFECT_COUNT": {
            "assertionKind": "TASK_COMPLETION",
            "effect": "CREATE_TEST_REQUEST",
            "maxCount": MAX_RULE_EFFECT_COUNT,
        },
        "READER_NEXT_SEQUENCE": {
            "assertionKind": "READING_ORDER",
            "action": "NEXT",
            "minSteps": 2,
            "maxSteps": MAX_READER_SEQUENCE_STEPS,
            "maxActionSequence": MAX_RULE_ACTION_SEQUENCE,
            "maxPhraseCharacters": MAX_RULE_PHRASE_CHARACTERS,
            "maxTotalPhraseBytes": MAX_RULE_PHRASE_BYTES,
        },
    }


@dataclass(frozen=True, slots=True)
class ReaderSequenceStep:
    action_sequence: int
    phrase: str

    def __post_init__(self) -> None:
        if (
            type(self.action_sequence) is not int
            or not 1 <= self.action_sequence <= MAX_RULE_ACTION_SEQUENCE
            or not isinstance(self.phrase, str)
            or not self.phrase.strip()
            or len(self.phrase) > MAX_RULE_PHRASE_CHARACTERS
            or len(self.phrase.encode()) > MAX_RULE_PHRASE_BYTES
        ):
            raise ValueError("reader sequence step requires a bounded action and exact phrase")


@dataclass(frozen=True, slots=True)
class EvaluationRule:
    """Literal, frozen predicates; never interpret prose, run regex/code or invent a matcher.

    A phrase predicate describes one captured utterance after an exact action, not every speech
    event in an inferred time window. Additional observation kinds need their own typed contracts.
    """

    rule_type: str
    action_sequence: int | None = None
    phrase: str | None = None
    effect: str | None = None
    count: int | None = None
    steps: tuple[ReaderSequenceStep, ...] = ()

    def __post_init__(self) -> None:
        if self.rule_type == "EXACT_READER_PHRASE":
            if (
                type(self.action_sequence) is not int
                or not 1 <= self.action_sequence <= MAX_RULE_ACTION_SEQUENCE
                or not isinstance(self.phrase, str)
                or not self.phrase.strip()
                or len(self.phrase) > MAX_RULE_PHRASE_CHARACTERS
                or len(self.phrase.encode()) > MAX_RULE_PHRASE_BYTES
                or self.effect is not None
                or self.count is not None
                or self.steps != ()
            ):
                raise ValueError("phrase rule requires a bounded exact action sequence and phrase")
        elif self.rule_type == "EFFECT_COUNT":
            if (
                self.effect != "CREATE_TEST_REQUEST"
                or type(self.count) is not int
                or not 0 <= self.count <= MAX_RULE_EFFECT_COUNT
                or self.action_sequence is not None
                or self.phrase is not None
                or self.steps != ()
            ):
                raise ValueError(
                    "effect rule requires a supported effect and bounded integer count"
                )
        elif self.rule_type == "READER_NEXT_SEQUENCE":
            if (
                type(self.steps) is not tuple
                or not 2 <= len(self.steps) <= MAX_READER_SEQUENCE_STEPS
                or any(not isinstance(step, ReaderSequenceStep) for step in self.steps)
                or any(
                    right.action_sequence != left.action_sequence + 1
                    for left, right in zip(self.steps, self.steps[1:], strict=False)
                )
                or sum(len(step.phrase.encode()) for step in self.steps) > MAX_RULE_PHRASE_BYTES
                or any(
                    v is not None
                    for v in (self.action_sequence, self.phrase, self.effect, self.count)
                )
            ):
                raise ValueError("reader sequence requires bounded consecutive NEXT steps")
        else:
            raise ValueError("unsupported evaluation rule; no inferred predicate")

    @classmethod
    def parse(cls, value: Any) -> EvaluationRule:
        if not isinstance(value, dict):
            raise ValueError("evaluationRule must be an object")
        if set(value) == {"type", "actionSequence", "phrase"}:
            return cls(
                value["type"], action_sequence=value["actionSequence"], phrase=value["phrase"]
            )
        if set(value) == {"type", "effect", "count"}:
            return cls(value["type"], effect=value["effect"], count=value["count"])
        if set(value) == {"type", "steps"} and isinstance(value["steps"], list):
            if any(
                not isinstance(step, dict) or set(step) != {"actionSequence", "phrase"}
                for step in value["steps"]
            ):
                raise ValueError("reader sequence step fields are incomplete or unsupported")
            return cls(
                value["type"],
                steps=tuple(
                    ReaderSequenceStep(step["actionSequence"], step["phrase"])
                    for step in value["steps"]
                ),
            )
        raise ValueError("evaluationRule fields are incomplete or unsupported")

    def canonical_form(self) -> dict[str, Any]:
        if self.rule_type == "READER_NEXT_SEQUENCE":
            return {
                "type": self.rule_type,
                "steps": [
                    {"actionSequence": step.action_sequence, "phrase": step.phrase}
                    for step in self.steps
                ],
            }
        if self.rule_type == "EXACT_READER_PHRASE":
            return {
                "type": self.rule_type,
                "actionSequence": self.action_sequence,
                "phrase": self.phrase,
            }
        return {"type": self.rule_type, "effect": self.effect, "count": self.count}


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
    evaluation_rule: EvaluationRule | None = None

    def __post_init__(self) -> None:
        if self.evaluation_rule is not None and (
            not isinstance(self.evaluation_rule, EvaluationRule)
            or (self.kind, self.evaluation_rule.rule_type)
            not in {
                (AssertionKind.REQUIRED_ANNOUNCEMENT, "EXACT_READER_PHRASE"),
                (AssertionKind.TASK_COMPLETION, "EFFECT_COUNT"),
                (AssertionKind.READING_ORDER, "READER_NEXT_SEQUENCE"),
            }
        ):
            raise ValueError("evaluation rule is not supported by this assertion's observer")
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
                    **(
                        {"evaluationRule": a.evaluation_rule.canonical_form()}
                        if a.evaluation_rule is not None
                        else {}
                    ),
                }
                for a in self.assertions
            ]
        }

    @classmethod
    def from_canonical_form(cls, value: Any) -> AssertionSet:
        """Read the original complete contract, refusing missing/altered observer identities."""
        if not isinstance(value, dict) or set(value) != {"assertions"}:
            raise ValueError("original assertion contract unavailable")
        if not isinstance(value["assertions"], list):
            raise ValueError("invalid assertion contract")
        built = []
        for item in value["assertions"]:
            if not isinstance(item, dict) or not {
                "assertionId",
                "kind",
                "description",
                "required",
                "observer",
                "unknownReasons",
            } <= set(item):
                raise ValueError("incomplete assertion contract")
            if (
                set(item)
                - {
                    "assertionId",
                    "kind",
                    "description",
                    "required",
                    "observer",
                    "unknownReasons",
                    "evaluationRule",
                }
                or type(item["required"]) is not bool
                or not isinstance(item["assertionId"], str)
                or not isinstance(item["description"], str)
                or not isinstance(item["unknownReasons"], list)
            ):
                raise ValueError("invalid assertion contract fields")
            assertion = Assertion(
                assertion_id=item["assertionId"],
                kind=AssertionKind(item["kind"]),
                description=item["description"],
                required=item["required"],
                unknown_reasons=frozenset(UnknownReason(r) for r in item["unknownReasons"]),
                evaluation_rule=EvaluationRule.parse(item["evaluationRule"])
                if "evaluationRule" in item
                else None,
            )
            if assertion.observer.value != item["observer"]:
                raise ValueError("assertion observer identity differs")
            built.append(assertion)
        result = cls(tuple(built))
        if result.canonical_form() != value:
            raise ValueError("assertion contract is not its original canonical form")
        return result
