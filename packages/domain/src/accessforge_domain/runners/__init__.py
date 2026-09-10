"""Runner identity, preflight proof and the supervisor action gate.

A runner is a supervisor process living in a *physical interactive desktop session* — a signed-in
macOS or Windows session with a real screen reader, a real browser and real focus. It is not a
container, not a process, and not a cloud function, and the distinction is the whole reason this
module exists. Two supervisor processes started on the same desktop are two processes and one
desktop; admitting both would put two attempts on one screen, one typing over the other's focus,
and every observation either of them made would be worthless (INV-10).

So identity here is deliberately *not* a process id, a container id, a hostname or a PID. It is the
operating system's own identifier for the interactive session — a macOS console `auid`/session id,
a Windows WTS session id — bound to a device identity, and everything downstream is keyed on that.

Three things in this module are pure and testable without a database or a desktop:

* :class:`PhysicalSession` — what "the same desktop" means, and what it deliberately excludes.
* :class:`PreflightResult` — a structured proof of readiness. A runner cannot assert READY; it
  submits evidence of specific checks and the *server* decides. Task 1 of the module prompt says
  this in one line: "do not accept self-asserted READY as successful preflight."
* :func:`evaluate_action` — the gate every OS action passes through, evaluated against lease epoch,
  monotonic deadline, cancellation, budget and allowlist, and failing closed on anything unknown.

Requirements: FR-004, FR-005, FR-014, FR-015, FR-021.
Invariants: INV-01, INV-06, INV-08, INV-09, INV-10, INV-13, INV-14.
"""

from .gate import (
    ActionDecision,
    ActionRequest,
    GateRefusal,
    LeaseView,
    evaluate_action,
)
from .identity import (
    EnrollmentError,
    PhysicalSession,
    RunnerProfile,
    profile_digest,
    session_key,
)
from .preflight import (
    REQUIRED_PREFLIGHT_CHECKS,
    AmbiguityReason,
    PreflightCheck,
    PreflightError,
    PreflightResult,
    QuarantineReason,
    admissible_runner_transitions,
    assert_runner_transition,
)

__all__ = [
    "REQUIRED_PREFLIGHT_CHECKS",
    "ActionDecision",
    "ActionRequest",
    "AmbiguityReason",
    "EnrollmentError",
    "GateRefusal",
    "LeaseView",
    "PhysicalSession",
    "PreflightCheck",
    "PreflightError",
    "PreflightResult",
    "QuarantineReason",
    "RunnerProfile",
    "admissible_runner_transitions",
    "assert_runner_transition",
    "evaluate_action",
    "profile_digest",
    "session_key",
]
