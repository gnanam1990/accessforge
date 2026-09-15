from dataclasses import replace

import pytest

from accessforge_domain.effect_monitor import (
    EffectCoverage,
    EffectInterval,
    EffectWindow,
    assess_effect_absence,
)
from accessforge_domain.states import Condition


def window() -> EffectWindow:
    return EffectWindow(
        run_id="11111111-1111-4111-8111-111111111111",
        attempt_id="22222222-2222-4222-8222-222222222222",
        scope_digest="a" * 64,
        clock_epoch="33333333-3333-4333-8333-333333333333",
        effect="SEND_EMAIL",
        start_ns=10,
        end_ns=30,
    )


@pytest.mark.parametrize(
    "intervals,expected",
    [
        ((), Condition.UNKNOWN),
        ((EffectInterval(10, 30, 0),), Condition.TRUE),
        ((EffectInterval(10, 20, 0), EffectInterval(20, 30, 0)), Condition.TRUE),
        ((EffectInterval(10, 20, 0), EffectInterval(21, 30, 0)), Condition.UNKNOWN),
        ((EffectInterval(11, 30, 0),), Condition.UNKNOWN),
        ((EffectInterval(10, 29, 0),), Condition.UNKNOWN),
        ((EffectInterval(29, 30, 0),), Condition.UNKNOWN),
        ((EffectInterval(10, 20, 1),), Condition.FALSE),
    ],
)
def test_absence_requires_complete_interval_not_final_zero(
    intervals: tuple[EffectInterval, ...], expected: Condition
) -> None:
    target = window()
    assert assess_effect_absence(target, EffectCoverage(target, intervals)) is expected


def test_unavailable_monitor_and_other_attempt_cannot_supply_evidence() -> None:
    target = window()
    assert assess_effect_absence(target, None) is Condition.UNKNOWN
    for other in (
        replace(target, run_id="44444444-4444-4444-8444-444444444444"),
        replace(target, attempt_id="44444444-4444-4444-8444-444444444444"),
        replace(target, clock_epoch="44444444-4444-4444-8444-444444444444"),
        replace(target, scope_digest="b" * 64),
        replace(target, effect="SEND_PAYMENT"),
    ):
        assert (
            assess_effect_absence(target, EffectCoverage(other, (EffectInterval(10, 30, 1),)))
            is Condition.UNKNOWN
        )


@pytest.mark.parametrize(
    "intervals",
    [
        (EffectInterval(9, 30, 0),),
        (EffectInterval(10, 31, 0),),
        (EffectInterval(10, 21, 0), EffectInterval(20, 30, 0)),
        (EffectInterval(20, 30, 0), EffectInterval(10, 20, 0)),
        (EffectInterval(10, 30, 0), EffectInterval(10, 30, 0)),
    ],
)
def test_duplicate_overlap_and_out_of_scope_measurements_rejected(
    intervals: tuple[EffectInterval, ...],
) -> None:
    with pytest.raises(ValueError):
        EffectCoverage(window(), intervals)


def test_boolean_counts_and_empty_windows_are_not_measurements() -> None:
    with pytest.raises(ValueError):
        EffectInterval(10, 30, False)
    with pytest.raises(ValueError):
        replace(window(), end_ns=10)
    with pytest.raises(ValueError):
        replace(window(), start_ns=True)
