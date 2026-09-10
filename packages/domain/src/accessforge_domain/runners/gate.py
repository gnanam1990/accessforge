"""The supervisor action gate.

TDD section 5: "Before each action, enforce current lease epoch, local TTL/watchdog, cancellation,
remaining budget, action schema, focus/origin and effect policy."

Every one of those is checked here, in one pure function, before a single keystroke reaches an
operating system. Three properties of the design are load-bearing:

**It fails closed.** The function returns a refusal for anything it cannot positively justify, and
the caller is given a value it must inspect rather than an exception it might catch too broadly.
There is no path that returns "allowed" by falling off the end.

**Deadlines are monotonic, audit timestamps are UTC.** These are different clocks for different
jobs and mixing them is a real vulnerability, not a style question. A wall clock can jump — NTP
correction, daylight saving, an operator setting the date, a VM resuming from a snapshot — and a
lease deadline compared against a wall clock jumps with it, in either direction: backwards and the
lease appears to have decades left; forwards and it expires mid-action. So the deadline is a
:func:`time.monotonic` reading, which is immune to all of that, while the UTC timestamp that goes
into the audit record stays wall-clock because an operator reading an incident timeline needs a
time they recognise. The gate takes both and never substitutes one for the other.

**A cancellation request fences immediately.** Not when acknowledged, not when the run reaches a
terminal state: the moment ``cancel_requested`` is set, no further action is admitted (INV-13).
Cancellation being non-terminal is exactly why the fence has to live here — the run is still
RUNNING, the lease is still valid, and the only thing stopping the next keystroke is this check.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from accessforge_domain.journeys.dsl import ALLOWED_ACTIONS, ALLOWED_KEY_CHORDS
from accessforge_domain.timestamps import parse_rfc3339_utc


class GateRefusal(StrEnum):
    """Why an action was not admitted.

    A closed vocabulary because these values are recorded as evidence and read by an operator
    deciding whether a desktop is safe to reuse. "Refused" with a free-text reason is not something
    a later test can assert on.
    """

    LEASE_EPOCH_STALE = "LEASE_EPOCH_STALE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    ACTION_BUDGET_EXHAUSTED = "ACTION_BUDGET_EXHAUSTED"
    WALL_TIME_BUDGET_EXHAUSTED = "WALL_TIME_BUDGET_EXHAUSTED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    KEY_CHORD_NOT_ALLOWED = "KEY_CHORD_NOT_ALLOWED"
    ORIGIN_NOT_PERMITTED = "ORIGIN_NOT_PERMITTED"
    ACTION_IN_FLIGHT = "ACTION_IN_FLIGHT"
    CLOCK_WENT_BACKWARDS = "CLOCK_WENT_BACKWARDS"
    MALFORMED_REQUEST = "MALFORMED_REQUEST"


@dataclass(frozen=True, slots=True)
class LeaseView:
    """What the supervisor locally believes about its own lease.

    Local belief on purpose. TDD section 5: "During network partitions the runner's local expiry
    stops new input." A supervisor that could only stop when the server told it to would keep
    typing into a live application for as long as the network stayed down, which is the failure
    mode a desktop lease exists to prevent. The server's view is authoritative for *admission*;
    this view is authoritative for *stopping*, and the two are deliberately not the same thing.
    """

    lease_id: str
    epoch: int
    current_epoch: int
    deadline_monotonic: float
    cancel_requested: bool
    action_in_flight: bool

    def __post_init__(self) -> None:
        if self.epoch < 1 or self.current_epoch < 1:
            raise ValueError("lease epochs are monotonic and start at 1")


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """One proposed operating-system action.

    ``origin`` is the origin the browser is actually on, as observed, not the origin the journey
    intended. Checking the intended one would check nothing: a navigation that went somewhere
    unexpected is precisely the case where the next keystroke must not be typed.
    """

    action: str
    now_monotonic: float
    now_utc: str
    actions_used: int
    max_actions: int
    wall_time_used_seconds: float
    max_wall_time_seconds: int
    platform: str
    origin: str
    permitted_origins: frozenset[str]
    key_chord: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class ActionDecision:
    """The gate's answer. ``admitted`` and ``refusal`` are never both meaningful.

    Returned rather than raised so that a caller cannot admit an action by forgetting a ``try``.
    The refusal is data the supervisor records before it does anything else.
    """

    admitted: bool
    refusal: GateRefusal | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.admitted and self.refusal is not None:
            raise ValueError("an admitted action has no refusal")
        if not self.admitted and self.refusal is None:
            raise ValueError("a refused action must say why")


_ADMITTED = ActionDecision(admitted=True)


def _refuse(reason: GateRefusal, detail: str) -> ActionDecision:
    return ActionDecision(admitted=False, refusal=reason, detail=detail)


def evaluate_action(lease: LeaseView, request: ActionRequest) -> ActionDecision:
    """Decide whether one action may reach the operating system.

    The order of the checks is not arbitrary. Identity and authority come before resources, and
    resources come before schema, so that the refusal an operator sees names the most fundamental
    thing that was wrong. A run whose lease was superseded and whose budget was also exhausted has
    a stale-epoch problem; reporting the budget would send someone to raise a limit that is not the
    issue.
    """
    if request.now_monotonic < 0:
        return _refuse(
            GateRefusal.MALFORMED_REQUEST,
            "a monotonic reading cannot be negative; this is not a monotonic clock",
        )
    if request.actions_used < 0 or request.wall_time_used_seconds < 0:
        return _refuse(GateRefusal.MALFORMED_REQUEST, "consumption cannot be negative")

    # Authority first: a superseded lease has no standing to spend a budget or type anything.
    if lease.epoch != lease.current_epoch:
        return _refuse(
            GateRefusal.LEASE_EPOCH_STALE,
            f"this supervisor holds epoch {lease.epoch} and the current epoch is "
            f"{lease.current_epoch}; another attempt has been admitted to this desktop since",
        )

    if request.now_monotonic >= lease.deadline_monotonic:
        return _refuse(
            GateRefusal.LEASE_EXPIRED,
            "the local lease deadline has passed; local expiry stops new input without waiting to "
            "hear from the server, because a partition is not permission to keep typing",
        )

    # Cancellation fences before the acknowledgement exists, which is the whole point: the run is
    # still RUNNING and the lease is still valid, so nothing else here would stop the next action.
    if lease.cancel_requested:
        return _refuse(
            GateRefusal.CANCELLATION_REQUESTED,
            "cancellation has been requested; no further action is admitted, and effects already "
            "performed remain recorded",
        )

    # An unresolved action is ambiguous, and a second action on top of an ambiguous one makes the
    # evidence unreadable: nobody can later say which of the two produced what the reader announced.
    if lease.action_in_flight:
        return _refuse(
            GateRefusal.ACTION_IN_FLIGHT,
            "an action is already dispatched and unresolved; its outcome is unknown and issuing "
            "another would make both unattributable",
        )

    if request.actions_used >= request.max_actions:
        return _refuse(
            GateRefusal.ACTION_BUDGET_EXHAUSTED,
            f"{request.actions_used} of {request.max_actions} actions used; work stops visibly "
            "rather than continuing on a raised limit nobody approved",
        )

    if request.wall_time_used_seconds >= request.max_wall_time_seconds:
        return _refuse(
            GateRefusal.WALL_TIME_BUDGET_EXHAUSTED,
            f"{request.wall_time_used_seconds:.1f}s of {request.max_wall_time_seconds}s used",
        )

    if request.action not in ALLOWED_ACTIONS:
        return _refuse(
            GateRefusal.ACTION_NOT_ALLOWED,
            f"{request.action!r} is not in the sealed action policy (INV-01); the allowlist is "
            f"{', '.join(sorted(ALLOWED_ACTIONS))}",
        )

    if request.action == "KEY_CHORD":
        allowed = ALLOWED_KEY_CHORDS.get(request.platform, frozenset())
        if request.key_chord is None:
            return _refuse(GateRefusal.MALFORMED_REQUEST, "KEY_CHORD without a chord")
        if request.key_chord not in allowed:
            return _refuse(
                GateRefusal.KEY_CHORD_NOT_ALLOWED,
                f"{request.key_chord!r} is not permitted on {request.platform}; the allowlist is "
                "reader and browser navigation only, and deliberately excludes anything reaching "
                "the address bar, the clipboard, developer tools or the operating system",
            )

    if request.action == "TYPE_TEXT" and request.text is None:
        return _refuse(GateRefusal.MALFORMED_REQUEST, "TYPE_TEXT without text")

    # STOP is the one action that must remain available when the page has gone somewhere it should
    # not have. Refusing it on an off-policy origin would strand the supervisor on exactly the page
    # it most needs to stop on.
    if request.action != "STOP" and request.origin not in request.permitted_origins:
        return _refuse(
            GateRefusal.ORIGIN_NOT_PERMITTED,
            f"the browser is on {request.origin!r}, which is not a permitted origin for this run; "
            "an action here would take effect somewhere nobody authorized",
        )

    return _ADMITTED


def assert_audit_timestamp(now_utc: str) -> None:
    """The audit timestamp stays wall-clock UTC even though the deadline is monotonic.

    Kept as a separate, explicitly named function because the temptation is to notice that the gate
    already has a monotonic reading and record that instead. A monotonic reading is meaningless in
    an incident timeline: it counts from an arbitrary origin, is not comparable across processes,
    and does not survive a reboot.
    """
    parse_rfc3339_utc(now_utc)
