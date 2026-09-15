"""Independent effect coverage, not a final-state count or a source authentication boundary.

A trusted collector must measure complete half-open intervals against a protected sink or
enforcement boundary. Polling application row counts cannot construct these measurements.
Source admission must authenticate the collector and bind the run, attempt, scope and clock
epoch before calling this module. A digest binds scope; it does not prove coverage or isolation.
"""

from dataclasses import dataclass
from uuid import UUID

from .states import Condition

EFFECTS = frozenset({"CREATE_TEST_REQUEST", "EXTERNAL_SUBMISSION", "SEND_EMAIL", "SEND_PAYMENT"})
MAX_INTERVALS = 1000


def _uuid(value: str) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _tick(value: int) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


@dataclass(frozen=True, slots=True)
class EffectWindow:
    """Supervisor-owned execution interval in one independent collector clock epoch.

    Scope digest must describe all monitored destinations/resources and enforcement boundaries.
    It must not be selected by the navigator or inferred from the observed effects.
    """

    run_id: str
    attempt_id: str
    scope_digest: str
    clock_epoch: str
    effect: str
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        if (
            not all(_uuid(v) for v in (self.run_id, self.attempt_id, self.clock_epoch))
            or not isinstance(self.scope_digest, str)
            or len(self.scope_digest) != 64
            or any(c not in "0123456789abcdef" for c in self.scope_digest)
            or not isinstance(self.effect, str)
            or self.effect not in EFFECTS
            or not _tick(self.start_ns)
            or not _tick(self.end_ns)
            or self.start_ns >= self.end_ns
        ):
            raise ValueError(
                "effect window requires an exact identity, scope and nonempty interval"
            )


@dataclass(frozen=True, slots=True)
class EffectInterval:
    """Continuously measured [start, end) interval, including transient occurrences.

    Zero means measured absence across this interval, never 'zero rows at the end'. An
    interruption creates a gap between intervals; it cannot be represented as a zero interval.
    """

    start_ns: int
    end_ns: int
    occurrences: int

    def __post_init__(self) -> None:
        if (
            not _tick(self.start_ns)
            or not _tick(self.end_ns)
            or self.start_ns >= self.end_ns
            or type(self.occurrences) is not int
            or not 0 <= self.occurrences <= 2**63 - 1
        ):
            raise ValueError(
                "effect interval requires bounded monotonic ticks and occurrence count"
            )


@dataclass(frozen=True, slots=True)
class EffectCoverage:
    window: EffectWindow
    intervals: tuple[EffectInterval, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.window, EffectWindow)
            or type(self.intervals) is not tuple
            or len(self.intervals) > MAX_INTERVALS
            or any(not isinstance(i, EffectInterval) for i in self.intervals)
        ):
            raise ValueError("effect coverage requires a bounded immutable measurement stream")
        if any(
            i.start_ns < self.window.start_ns or i.end_ns > self.window.end_ns
            for i in self.intervals
        ) or any(
            left.end_ns > right.start_ns
            for left, right in zip(self.intervals, self.intervals[1:], strict=False)
        ):
            raise ValueError(
                "effect coverage has out-of-window, overlapping or unordered intervals"
            )


def assess_effect_absence(expected: EffectWindow, measured: EffectCoverage | None) -> Condition:
    """No observer-authored assertion is minted here; authenticated retention remains required.

    An admitted occurrence disproves absence even if other intervals are missing. Absence needs
    complete coverage including both execution boundaries; unavailable or mismatched sources are
    UNKNOWN. No duration tolerance, extrapolation, count subtraction or cross-attempt stitching.
    """
    if not isinstance(expected, EffectWindow):
        raise ValueError("expected execution window must be supervisor-owned")
    if measured is None or measured.window != expected:
        return Condition.UNKNOWN
    if any(i.occurrences > 0 for i in measured.intervals):
        return Condition.FALSE
    cursor = expected.start_ns
    for interval in measured.intervals:
        if interval.start_ns != cursor:
            return Condition.UNKNOWN
        cursor = interval.end_ns
    return Condition.TRUE if cursor == expected.end_ns else Condition.UNKNOWN
