"""Canonical state vocabulary.

These names and their admissible pairings come from CONTRACTS section 5 and are not open to
local reinterpretation. No module may add a state: a new state would be a contract change
requiring PRD, CONTRACTS, TDD and TEST-PLAN to be reconciled together.
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Lifecycle position of a run.

    Normal progression is QUEUED -> LEASED -> RUNNING -> FINALIZING -> COMPLETED. Any nonterminal
    state may instead end INTERRUPTED or CANCELLED.
    """

    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


class Outcome(StrEnum):
    """The verdict of a run, separate from its status.

    Status says how the run ended; outcome says what it established. A run can complete
    successfully as a process and still establish nothing.
    """

    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"  # noqa: S105 - a verdict, not a credential
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class Condition(StrEnum):
    """Tri-state result of a frozen assertion or completion observation.

    UNKNOWN is a first-class value, not an error case to be coerced. Collapsing it into FALSE
    would turn "we could not observe this" into "this is broken", which is the specific
    misreporting INV-02 exists to prevent.
    """

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class FindingStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    REPRODUCED = "REPRODUCED"
    DISMISSED = "DISMISSED"
    RESOLVED = "RESOLVED"


class PatchStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    BUILDING = "BUILDING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    FAILED = "FAILED"


class ReviewVerdict(StrEnum):
    ACCEPT = "ACCEPT"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    UNABLE_TO_ASSESS = "UNABLE_TO_ASSESS"


class RunnerStatus(StrEnum):
    OFFLINE = "OFFLINE"
    PREFLIGHT_REQUIRED = "PREFLIGHT_REQUIRED"
    READY = "READY"
    BUSY = "BUSY"
    QUARANTINED = "QUARANTINED"


class ApprovalScope(StrEnum):
    """Authorization scopes.

    These do not nest and do not imply one another. RUN_EFFECTS never authorizes a patch;
    PATCH_APPLY authorizes only an isolated candidate workspace and never a merge or a
    deployment; GITHUB_PUBLISH binds one exact publication payload.
    """

    RUN_EFFECTS = "RUN_EFFECTS"
    PATCH_APPLY = "PATCH_APPLY"
    GITHUB_PUBLISH = "GITHUB_PUBLISH"


TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.INTERRUPTED, RunStatus.CANCELLED}
)

NONTERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.QUEUED, RunStatus.LEASED, RunStatus.RUNNING, RunStatus.FINALIZING}
)

# The forward lifecycle. Cancellation and interruption are handled separately because they are
# available from every nonterminal state rather than following this chain.
NORMAL_PROGRESSION: dict[RunStatus, RunStatus] = {
    RunStatus.QUEUED: RunStatus.LEASED,
    RunStatus.LEASED: RunStatus.RUNNING,
    RunStatus.RUNNING: RunStatus.FINALIZING,
    RunStatus.FINALIZING: RunStatus.COMPLETED,
}


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL_STATUSES


def admissible_outcomes(status: RunStatus, *, execution_began: bool = False) -> frozenset[Outcome]:
    """Outcomes a run in ``status`` may legitimately hold.

    ``execution_began`` distinguishes the two cancellation cases: a run cancelled before any
    action was ever admitted evaluated nothing (NOT_EVALUATED), while one cancelled after
    execution started evaluated something incompletely (INCONCLUSIVE).
    """
    if status in NONTERMINAL_STATUSES:
        return frozenset({Outcome.NOT_EVALUATED})
    if status is RunStatus.COMPLETED:
        # Never NOT_EVALUATED: a completed run has been evaluated by definition.
        return frozenset({Outcome.PASS, Outcome.FAIL, Outcome.INCONCLUSIVE})
    if status is RunStatus.INTERRUPTED:
        return frozenset({Outcome.INCONCLUSIVE})
    if status is RunStatus.CANCELLED:
        return frozenset({Outcome.INCONCLUSIVE} if execution_began else {Outcome.NOT_EVALUATED})
    raise AssertionError(f"unhandled status {status!r}")


def is_admissible_pair(
    status: RunStatus, outcome: Outcome, *, execution_began: bool = False
) -> bool:
    return outcome in admissible_outcomes(status, execution_began=execution_began)
