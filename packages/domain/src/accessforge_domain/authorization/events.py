"""Which service identity may submit which kind of evidence.

This is the rule that makes run outcomes mean anything. The supervisor drives the desktop and can
see what the screen reader said; the independent observer reads the application's own durable state.
If the supervisor could emit an ``EFFECT_RECEIPT``, a run could claim the task succeeded on the
strength of the same component that was trying to make it succeed.

So the ACL below is not defence in depth around a trusted path — it *is* the separation that
CONTRACTS section 7 requires, and it is enforced here rather than in a prompt or a code review
convention.
"""

from __future__ import annotations

from .principals import MachinePrincipal, ServiceIdentity

# Event kinds come from CONTRACTS section 7. Kept as plain strings here rather than importing the
# generated enum, so that this module stays pure domain logic with no contracts dependency; the
# contract tests assert the two agree.
_SUPERVISOR_KINDS = frozenset(
    {
        "RUN_STARTED",
        "PREFLIGHT_RESULT",
        "ACTION_INTENT",
        "ACTION_RESULT",
        "READER_OBSERVATION",
        "BUDGET_EVENT",
        "INTERRUPTION",
        "RUN_FINISHED",
    }
)

_OBSERVER_KINDS = frozenset(
    {
        "EFFECT_RECEIPT",
        "ASSERTION_OBSERVATION",
    }
)

EVENT_PRODUCER_ACL: dict[ServiceIdentity, frozenset[str]] = {
    ServiceIdentity.SUPERVISOR: _SUPERVISOR_KINDS,
    ServiceIdentity.OBSERVER: _OBSERVER_KINDS,
    # The navigator proposes actions to its supervisor; it never submits evidence, so forging a
    # record is not something it can attempt rather than something it is trusted not to do.
    ServiceIdentity.NAVIGATOR: frozenset(),
    # The orchestrator plans; the builder builds. Neither is an evidence producer.
    ServiceIdentity.ORCHESTRATOR: frozenset(),
    ServiceIdentity.BUILDER: frozenset(),
    # Ingestion is the sequencer, not a producer. It assigns canonical order to records others
    # submitted; if it could also author them the chain would attest only to itself.
    ServiceIdentity.INGESTION: frozenset(),
}

# Stated explicitly so a future change cannot quietly create an overlap. These two identities must
# never share an event kind.
assert not (_SUPERVISOR_KINDS & _OBSERVER_KINDS), (
    "supervisor and observer event kinds must not overlap"
)


class EventSubmissionError(Exception):
    """A principal attempted to submit an event kind it may not produce."""


def may_submit_event(principal: MachinePrincipal, event_type: str) -> bool:
    return event_type in EVENT_PRODUCER_ACL.get(principal.service_identity, frozenset())


def assert_may_submit_event(principal: MachinePrincipal, event_type: str) -> None:
    """Raise unless this identity may produce this event kind.

    The error names both sides, because the interesting failure in production is a component
    misconfigured with the wrong identity, and a bare "forbidden" would not distinguish that from
    an attack.
    """
    if not may_submit_event(principal, event_type):
        allowed = sorted(EVENT_PRODUCER_ACL.get(principal.service_identity, frozenset()))
        raise EventSubmissionError(
            f"{principal.service_identity} may not submit {event_type!r}; "
            f"permitted kinds: {allowed or 'none'}"
        )


def assert_may_dispatch_os_action(principal: MachinePrincipal) -> None:
    """Only a supervisor holding a desktop lease may dispatch an action to the operating system.

    The observer is deliberately excluded even though it is a trusted component: it verifies
    application state and must not be able to influence the journey it is verifying.
    """
    if principal.service_identity is not ServiceIdentity.SUPERVISOR:
        raise EventSubmissionError(
            f"{principal.service_identity} may not dispatch operating-system actions; "
            "only a leased supervisor may"
        )
    if not principal.lease_id:
        raise EventSubmissionError(
            "a supervisor without a desktop lease may not dispatch operating-system actions"
        )
