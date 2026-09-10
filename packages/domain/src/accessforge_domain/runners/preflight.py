"""Preflight: evidence of readiness, never an assertion of it.

TDD section 5 lists what preflight has to prove: "correct active reader and browser; capture works;
permitted origin reachable; reset succeeded; correct build identity; active session is usable; no
old process can still send input; local journal/storage and clock/lease checks healthy."

Two design decisions follow from that list, and both are structural rather than procedural.

First, a runner submits *check results*, and the server decides READY. There is no field on a
preflight submission that says "ready". A supervisor with a bug, a stale build, or an attacker's
credential can claim anything it likes about itself; it cannot claim a check it did not run,
because the vocabulary of checks is closed and every required one must be present and TRUE.

Second, an absent check is not a passing check. :data:`REQUIRED_PREFLIGHT_CHECKS` is compared for
*equality of coverage*, so a supervisor that silently stops reporting `NO_STALE_INPUT_SOURCE` after
an upgrade fails preflight instead of quietly losing the guarantee. This is INV-02's shape applied
to readiness: missing evidence never becomes a positive result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from accessforge_domain.states import Condition, RunnerStatus
from accessforge_domain.timestamps import parse_rfc3339_utc


class PreflightError(ValueError):
    """A preflight submission is malformed, incomplete, or claims what it cannot show."""


class PreflightCheck(StrEnum):
    """The closed vocabulary of things preflight can establish.

    Closed on purpose. If a runner could name its own checks, "READER_SPEECH_CAPTURED" and
    "reader_speech_captured" and "SPEECH_OK" would all appear over time, and the required-coverage
    comparison below would silently stop covering anything.
    """

    READER_ACTIVE = "READER_ACTIVE"
    READER_VERSION_MATCHES_PROFILE = "READER_VERSION_MATCHES_PROFILE"
    BROWSER_VERSION_MATCHES_PROFILE = "BROWSER_VERSION_MATCHES_PROFILE"
    SPEECH_CAPTURE_WORKING = "SPEECH_CAPTURE_WORKING"
    PERMITTED_ORIGIN_REACHABLE = "PERMITTED_ORIGIN_REACHABLE"
    ENVIRONMENT_RESET_SUCCEEDED = "ENVIRONMENT_RESET_SUCCEEDED"
    BUILD_IDENTITY_MATCHES_MANIFEST = "BUILD_IDENTITY_MATCHES_MANIFEST"
    DESKTOP_SESSION_OWNED = "DESKTOP_SESSION_OWNED"
    SCREEN_UNLOCKED = "SCREEN_UNLOCKED"
    ACCESSIBILITY_PERMISSION_GRANTED = "ACCESSIBILITY_PERMISSION_GRANTED"
    AUTOMATION_PERMISSION_GRANTED = "AUTOMATION_PERMISSION_GRANTED"
    NO_STALE_INPUT_SOURCE = "NO_STALE_INPUT_SOURCE"
    LOCAL_JOURNAL_WRITABLE = "LOCAL_JOURNAL_WRITABLE"
    MONOTONIC_CLOCK_HEALTHY = "MONOTONIC_CLOCK_HEALTHY"


#: Every check above is required. There is no optional preflight check, because an optional proof
#: of desktop safety is not a proof of desktop safety. The set is written out rather than derived
#: from the enum so that adding a check to the enum is a deliberate decision about whether runs may
#: proceed without it, taken here, in one place, and visible in a diff.
REQUIRED_PREFLIGHT_CHECKS: frozenset[PreflightCheck] = frozenset(
    {
        PreflightCheck.READER_ACTIVE,
        PreflightCheck.READER_VERSION_MATCHES_PROFILE,
        PreflightCheck.BROWSER_VERSION_MATCHES_PROFILE,
        PreflightCheck.SPEECH_CAPTURE_WORKING,
        PreflightCheck.PERMITTED_ORIGIN_REACHABLE,
        PreflightCheck.ENVIRONMENT_RESET_SUCCEEDED,
        PreflightCheck.BUILD_IDENTITY_MATCHES_MANIFEST,
        PreflightCheck.DESKTOP_SESSION_OWNED,
        PreflightCheck.SCREEN_UNLOCKED,
        PreflightCheck.ACCESSIBILITY_PERMISSION_GRANTED,
        PreflightCheck.AUTOMATION_PERMISSION_GRANTED,
        PreflightCheck.NO_STALE_INPUT_SOURCE,
        PreflightCheck.LOCAL_JOURNAL_WRITABLE,
        PreflightCheck.MONOTONIC_CLOCK_HEALTHY,
    }
)


class QuarantineReason(StrEnum):
    """Why a desktop is not available, in the runner's own vocabulary.

    Quarantine is a real state with a real cause, not a UI badge. The module prompt is explicit:
    "Do not reduce quarantine to a cosmetic UI state." A reason is required to enter it, and the
    reason is what an operator reads before deciding whether a reset is safe.
    """

    LEASE_EXPIRED_WITHOUT_STOP_PROOF = "LEASE_EXPIRED_WITHOUT_STOP_PROOF"
    AMBIGUOUS_ACTION = "AMBIGUOUS_ACTION"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    PROFILE_DRIFTED = "PROFILE_DRIFTED"
    RESET_FAILED = "RESET_FAILED"
    STALE_EPOCH_SUBMISSION = "STALE_EPOCH_SUBMISSION"
    OPERATOR_REQUESTED = "OPERATOR_REQUESTED"


class AmbiguityReason(StrEnum):
    """Why an attempt ended INTERRUPTED rather than CANCELLED or COMPLETED.

    CONTRACTS section 4: "Lost acknowledgement or ambiguous dispatched action ends INTERRUPTED with
    ``ambiguityReason``". Each value here names something the system genuinely does not know, and
    the honest answer to not knowing whether a keystroke reached a live application is not to guess
    and not to retry (INV-09).
    """

    ACTION_RESULT_NEVER_ARRIVED = "ACTION_RESULT_NEVER_ARRIVED"
    STOP_ACKNOWLEDGEMENT_LOST = "STOP_ACKNOWLEDGEMENT_LOST"
    LEASE_EXPIRED_MID_ACTION = "LEASE_EXPIRED_MID_ACTION"
    SUPERVISOR_CRASHED_AFTER_INTENT = "SUPERVISOR_CRASHED_AFTER_INTENT"
    CLOCK_DISCONTINUITY = "CLOCK_DISCONTINUITY"


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """One structured preflight submission.

    ``checks`` maps each check to a tri-state :class:`~accessforge_domain.states.Condition`. UNKNOWN
    is available and is *not* a pass: a runner that could not determine whether the screen was
    locked has not shown that the screen was unlocked.

    The binding fields are what make this result about one specific environment. A preflight is
    evidence for the profile, environment and manifest it names and for nothing else — reusing a
    yesterday's preflight after a reader upgrade is exactly the profile drift that must be caught.
    """

    runner_profile_digest: str
    environment_config_digest: str
    manifest_digest: str
    observed_reader_version: str
    observed_browser_version: str
    observed_locale: str
    observed_keyboard_layout: str
    desktop_session_key: str
    observed_at: str
    checks: dict[PreflightCheck, Condition]

    def __post_init__(self) -> None:
        parse_rfc3339_utc(self.observed_at)
        for name in (
            "runner_profile_digest",
            "environment_config_digest",
            "manifest_digest",
            "desktop_session_key",
        ):
            if not str(getattr(self, name)).strip():
                raise PreflightError(
                    f"{name} is empty; a preflight that names no {name.replace('_', ' ')} is "
                    "evidence about nothing in particular"
                )
        unknown = sorted(str(k) for k in self.checks if not isinstance(k, PreflightCheck))
        if unknown:
            raise PreflightError(
                f"preflight reported checks outside the closed vocabulary: {', '.join(unknown)}. "
                "A check nobody defined cannot be required, so it cannot contribute to readiness."
            )

    @property
    def missing_checks(self) -> frozenset[PreflightCheck]:
        """Required checks the runner did not report at all.

        Distinguished from failed checks throughout, because they mean different things to an
        operator: a failed check says the desktop is not ready, a missing one says the supervisor
        is not telling us whether it is.
        """
        return frozenset(REQUIRED_PREFLIGHT_CHECKS - set(self.checks))

    @property
    def failed_checks(self) -> frozenset[PreflightCheck]:
        return frozenset(
            check
            for check in REQUIRED_PREFLIGHT_CHECKS
            if self.checks.get(check, Condition.UNKNOWN) is Condition.FALSE
        )

    @property
    def unknown_checks(self) -> frozenset[PreflightCheck]:
        return frozenset(
            check
            for check in REQUIRED_PREFLIGHT_CHECKS
            if self.checks.get(check) is Condition.UNKNOWN
        )

    def is_successful(self) -> bool:
        """True only when every required check was reported and every one of them is TRUE.

        There is no `ready` field to read and no majority to take. This is the single place that
        turns evidence into readiness, and it is a conjunction over a closed set.
        """
        return not (self.missing_checks or self.failed_checks or self.unknown_checks)

    def refusal_summary(self) -> str:
        """One operator-readable sentence saying why this desktop is not ready."""
        if self.is_successful():
            return ""
        parts: list[str] = []
        if self.failed_checks:
            parts.append(f"failed: {', '.join(sorted(self.failed_checks))}")
        if self.unknown_checks:
            parts.append(f"could not be determined: {', '.join(sorted(self.unknown_checks))}")
        if self.missing_checks:
            parts.append(f"not reported at all: {', '.join(sorted(self.missing_checks))}")
        return "; ".join(parts)


#: Admissible runner transitions (CONTRACTS section 5).
#:
#: The shape worth reading carefully is what leaves QUARANTINED: only PREFLIGHT_REQUIRED. A
#: quarantined desktop cannot become READY, and it certainly cannot become BUSY. It has to be
#: reset, and then it has to prove itself again with a real preflight. "Expired lease fences the
#: runner; no new lease until reset/preflight confirms the previous session cannot still act."
_ADMISSIBLE: dict[RunnerStatus, frozenset[RunnerStatus]] = {
    RunnerStatus.OFFLINE: frozenset({RunnerStatus.PREFLIGHT_REQUIRED, RunnerStatus.QUARANTINED}),
    RunnerStatus.PREFLIGHT_REQUIRED: frozenset(
        {RunnerStatus.READY, RunnerStatus.OFFLINE, RunnerStatus.QUARANTINED}
    ),
    RunnerStatus.READY: frozenset(
        {RunnerStatus.BUSY, RunnerStatus.OFFLINE, RunnerStatus.QUARANTINED}
    ),
    RunnerStatus.BUSY: frozenset(
        {RunnerStatus.PREFLIGHT_REQUIRED, RunnerStatus.OFFLINE, RunnerStatus.QUARANTINED}
    ),
    RunnerStatus.QUARANTINED: frozenset({RunnerStatus.PREFLIGHT_REQUIRED}),
}


def admissible_runner_transitions(status: RunnerStatus) -> frozenset[RunnerStatus]:
    return _ADMISSIBLE[status]


def assert_runner_transition(
    current: RunnerStatus,
    proposed: RunnerStatus,
    *,
    preflight: PreflightResult | None = None,
) -> None:
    """Refuse a runner transition the contract does not allow.

    ``preflight`` is required for any transition *into* READY and must be successful. There is no
    other doorway: a runner becomes available for work by having produced evidence, which is what
    makes "do not accept self-asserted READY" enforceable rather than aspirational.
    """
    if proposed not in _ADMISSIBLE[current]:
        allowed = ", ".join(sorted(_ADMISSIBLE[current])) or "nothing"
        raise PreflightError(
            f"a runner in {current} cannot become {proposed}; admissible next states are {allowed}"
        )

    if proposed is RunnerStatus.READY:
        if preflight is None:
            raise PreflightError(
                "READY requires a preflight result. A runner cannot report itself ready: readiness "
                "is a server conclusion drawn from checks the runner performed, and with no checks "
                "there is nothing to conclude it from."
            )
        if not preflight.is_successful():
            raise PreflightError(
                f"preflight did not establish readiness -- {preflight.refusal_summary()}"
            )
