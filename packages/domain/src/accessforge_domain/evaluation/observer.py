"""The independent completion observer.

The one component that can say whether the task actually happened, and the reason a screen-reader
result means anything at all.

Here is the failure it exists to prevent. A navigator drives a form, the page shows "Request
submitted", the reader announces "Request submitted", every reader assertion is TRUE — and the
backend received nothing, because the submit handler threw and the toast is rendered optimistically.
Every observation in that run is real and the conclusion drawn from them is false. So completion is
decided by reading the application's own durable state, not by reading what the page said about it.

Four properties are structural rather than conventional:

**Read-only.** The observer's contract exposes no write. A component that could modify the
application under test could make its own assertion true, and the fact that it would not is not a
property anyone can check.

**Fresh fixture identity.** The observer counts effects belonging to *this run's* fixture nonce.
Without that, a receipt from a previous run satisfies this run's completion assertion — which is why
module 06 gives every run a distinct instance.

**Unavailability is UNKNOWN, never FALSE and never inferred success.** If the observer cannot reach
the application, that is not evidence the task failed, and the absence of an error is not evidence
it
succeeded. The module prompt names the specific misreading to avoid: no success is inferred "from
request submission or frontend toast appearance".

**Its configuration never reaches the navigator.** Enforced upstream by module 06's three-way
fixture
split; restated here as a type that carries no oracle values, so a caller holding an observation
cannot read the expectation it was compared against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from accessforge_domain.states import Condition


class ObserverError(Exception):
    """The observer could not establish what happened. Always an unknown, never a failure."""


@dataclass(frozen=True, slots=True)
class EffectCount:
    """What the observer measured in the application's durable state.

    Deliberately not "the receipt". A receipt is something the application hands out and an agent
    could fabricate; this is a count of rows the application actually holds, keyed on this run's
    fixture nonce.
    """

    fixture_nonce: str
    effect: str
    count: int
    observed_at: str


class CompletionObserver(Protocol):
    """The read-only interface a completion observer implements.

    One method, and no write anywhere in the protocol. Adding one would be visible in a diff, which
    is the point: this is the boundary between "reports what happened" and "can make it happen".
    """

    def count_effects(self, *, fixture_nonce: str, effect: str) -> EffectCount: ...


@dataclass(frozen=True, slots=True)
class CompletionObservation:
    """The observer's answer, with no oracle material in it.

    ``expected`` is absent by construction. A caller holding this cannot read what the count was
    compared against, which is what keeps the answer key out of anything downstream that might
    otherwise carry it towards the navigator.
    """

    condition: Condition
    detail: str
    observed_count: int | None = None


def observe_completion(
    observer: CompletionObserver,
    *,
    fixture_nonce: str,
    effect: str,
    expected_count: int,
) -> CompletionObservation:
    """Decide the frozen completion condition from the application's own state.

    ``expected_count`` is passed in and not returned. It comes from the observer configuration that
    module 06 keeps on the trusted side of the fixture split, and it stops here.

    An observer failure produces UNKNOWN with the cause. That is the branch that matters: it would
    be
    easy to treat "cannot reach the application" as FALSE, and doing so would report a confirmed
    task failure every time the observer's network hiccuped.
    """
    try:
        measured = observer.count_effects(fixture_nonce=fixture_nonce, effect=effect)
    except ObserverError as exc:
        return CompletionObservation(
            condition=Condition.UNKNOWN,
            detail=(
                f"the completion observer could not read the application's state: {exc}. This is "
                "unknown, not failure: being unable to look is not evidence that nothing happened, "
                "and the absence of an error is not evidence that something did."
            ),
        )

    if measured.fixture_nonce != fixture_nonce:
        # The prior-fixture-receipt case. A receipt from an earlier run has a different nonce, and
        # accepting it would let last week's success satisfy this run's completion assertion.
        return CompletionObservation(
            condition=Condition.UNKNOWN,
            detail=(
                f"the observation is for fixture {measured.fixture_nonce} and this run's "
                f"fixture is {fixture_nonce}. An effect belonging to a different run's fixture "
                "says nothing about this one."
            ),
        )

    if measured.count == expected_count:
        return CompletionObservation(
            condition=Condition.TRUE,
            detail=f"the application holds {measured.count} {effect} for this run's fixture",
            observed_count=measured.count,
        )

    return CompletionObservation(
        condition=Condition.FALSE,
        detail=(
            f"the application holds {measured.count} {effect} for this run's fixture. This is a "
            "measurement of durable state, not a reading of what the page displayed: a page can "
            "announce that a request was submitted having submitted nothing."
        ),
        observed_count=measured.count,
    )


def observer_writes_nothing(observer_type: type) -> tuple[str, ...]:
    """Method names on an observer implementation that look like writes.

    Used by a test rather than at runtime. A runtime check would be theatre -- an implementation
    determined to write can do so inside `count_effects` -- but as a test it catches the realistic
    version of this mistake, which is someone adding a `reset_fixture` helper to the observer
    because
    it already has a connection to the application and it seemed convenient.
    """
    suspicious = (
        "create",
        "write",
        "update",
        "delete",
        "insert",
        "reset",
        "submit",
        "post",
        "set_",
    )
    return tuple(
        name
        for name in dir(observer_type)
        if not name.startswith("_") and any(name.lower().startswith(s) for s in suspicious)
    )
