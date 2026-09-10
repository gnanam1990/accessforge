"""The gate every operating-system action passes through.

Negative cases from the module prompt that live at this layer: stale epoch acts, offline runner acts
after expiry, cancel arrives between intent and dispatch, clock jumps, budget exhaustion, and an
action outside the sealed policy.

Requirements: FR-005, FR-014, FR-015. Invariants: INV-01, INV-08, INV-13, INV-14.
"""

from __future__ import annotations

import pytest

from accessforge_domain.runners import (
    ActionRequest,
    GateRefusal,
    LeaseView,
    evaluate_action,
)
from accessforge_domain.runners.gate import assert_audit_timestamp
from accessforge_domain.timestamps import TimestampError

ORIGIN = "http://127.0.0.1:8081"
PERMITTED = frozenset({ORIGIN})


def _lease(**overrides: object) -> LeaseView:
    fields: dict[str, object] = {
        "lease_id": "lease-1",
        "epoch": 4,
        "current_epoch": 4,
        "deadline_monotonic": 1_000.0,
        "cancel_requested": False,
        "action_in_flight": False,
    }
    fields.update(overrides)
    return LeaseView(**fields)  # type: ignore[arg-type]


def _request(**overrides: object) -> ActionRequest:
    fields: dict[str, object] = {
        "action": "NEXT",
        "now_monotonic": 500.0,
        "now_utc": "2026-09-10T12:00:00.000000Z",
        "actions_used": 3,
        "max_actions": 50,
        "wall_time_used_seconds": 12.0,
        "max_wall_time_seconds": 600,
        "platform": "darwin",
        "origin": ORIGIN,
        "permitted_origins": PERMITTED,
    }
    fields.update(overrides)
    return ActionRequest(**fields)  # type: ignore[arg-type]


def _refusal(lease: LeaseView, request: ActionRequest) -> GateRefusal:
    decision = evaluate_action(lease, request)
    assert not decision.admitted, "expected a refusal"
    assert decision.refusal is not None
    return decision.refusal


# --- the allowed path ----------------------------------------------------------------------------


def test_a_valid_action_on_a_valid_lease_is_admitted() -> None:
    """Control. Without this every assertion below could pass on a gate that refuses everything."""
    decision = evaluate_action(_lease(), _request())
    assert decision.admitted
    assert decision.refusal is None


def test_every_allowlisted_action_is_admissible() -> None:
    from accessforge_domain.journeys.dsl import ALLOWED_ACTIONS

    for action in ALLOWED_ACTIONS:
        request = _request(
            action=action,
            key_chord="TAB" if action == "KEY_CHORD" else None,
            text="Test Person" if action == "TYPE_TEXT" else None,
        )
        assert evaluate_action(_lease(), request).admitted, action


# --- authority -----------------------------------------------------------------------------------


def test_a_stale_epoch_is_refused() -> None:
    """A partitioned supervisor that reconnects holds an epoch the desktop has moved past."""
    assert _refusal(_lease(epoch=4, current_epoch=5), _request()) is GateRefusal.LEASE_EPOCH_STALE


def test_a_stale_epoch_outranks_an_exhausted_budget() -> None:
    """Ordering matters for diagnosis: the budget is not the problem to go and fix."""
    refusal = _refusal(_lease(epoch=4, current_epoch=5), _request(actions_used=50, max_actions=50))
    assert refusal is GateRefusal.LEASE_EPOCH_STALE


def test_an_expired_local_deadline_is_refused() -> None:
    """The server does not have to say so. A partition is not permission to keep typing."""
    assert (
        _refusal(_lease(deadline_monotonic=400.0), _request(now_monotonic=500.0))
        is GateRefusal.LEASE_EXPIRED
    )


def test_the_deadline_boundary_is_exclusive() -> None:
    """At exactly the deadline the lease is over. An inclusive comparison admits one last keystroke
    at the moment the server may already have fenced the session."""
    assert (
        _refusal(_lease(deadline_monotonic=500.0), _request(now_monotonic=500.0))
        is GateRefusal.LEASE_EXPIRED
    )
    assert evaluate_action(
        _lease(deadline_monotonic=500.001), _request(now_monotonic=500.0)
    ).admitted


def test_a_wall_clock_jump_cannot_extend_a_lease() -> None:
    """The reason the deadline is monotonic at all.

    The UTC timestamp moves a year into the past -- an NTP correction, a VM restored from a snapshot
    -- and the decision does not change, because the deadline was never compared against it.
    """
    jumped = _request(now_utc="2025-01-01T00:00:00.000000Z", now_monotonic=1_500.0)
    assert _refusal(_lease(deadline_monotonic=1_000.0), jumped) is GateRefusal.LEASE_EXPIRED


def test_a_wall_clock_jump_backwards_cannot_revive_an_expired_lease() -> None:
    past = _request(now_utc="2020-01-01T00:00:00.000000Z", now_monotonic=2_000.0)
    assert _refusal(_lease(deadline_monotonic=1_000.0), past) is GateRefusal.LEASE_EXPIRED


def test_a_negative_monotonic_reading_is_not_a_monotonic_clock() -> None:
    assert _refusal(_lease(), _request(now_monotonic=-1.0)) is GateRefusal.MALFORMED_REQUEST


def test_an_epoch_below_one_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="monotonic and start at 1"):
        _lease(epoch=0)


# --- cancellation --------------------------------------------------------------------------------


def test_a_cancellation_request_fences_the_next_action() -> None:
    """INV-13, and the case the module prompt calls "cancel arrives between intent and dispatch".

    The run is still RUNNING, the lease is still valid and unexpired, and the budget is fine. This
    check is the only thing standing between a requested cancellation and another keystroke.
    """
    assert _refusal(_lease(cancel_requested=True), _request()) is GateRefusal.CANCELLATION_REQUESTED


def test_stop_is_also_refused_once_cancellation_is_requested() -> None:
    """Deliberate. Stopping is the supervisor's own business, not an admitted OS action, and
    admitting one action after cancellation would make "no further action" untrue."""
    assert (
        _refusal(_lease(cancel_requested=True), _request(action="STOP"))
        is GateRefusal.CANCELLATION_REQUESTED
    )


def test_an_unresolved_action_blocks_the_next_one() -> None:
    """Two actions over one unknown outcome make both unattributable in the evidence."""
    assert _refusal(_lease(action_in_flight=True), _request()) is GateRefusal.ACTION_IN_FLIGHT


# --- budgets -------------------------------------------------------------------------------------


def test_an_exhausted_action_budget_stops_work() -> None:
    """INV-14: visibly, and without substituting a result."""
    assert (
        _refusal(_lease(), _request(actions_used=50, max_actions=50))
        is GateRefusal.ACTION_BUDGET_EXHAUSTED
    )


def test_the_action_budget_boundary_is_the_last_allowed_action() -> None:
    assert evaluate_action(_lease(), _request(actions_used=49, max_actions=50)).admitted


def test_an_exhausted_wall_time_budget_stops_work() -> None:
    assert (
        _refusal(_lease(), _request(wall_time_used_seconds=600.0, max_wall_time_seconds=600))
        is GateRefusal.WALL_TIME_BUDGET_EXHAUSTED
    )


def test_negative_consumption_is_refused() -> None:
    """A negative count would make any budget comparison pass."""
    assert _refusal(_lease(), _request(actions_used=-1)) is GateRefusal.MALFORMED_REQUEST
    assert (
        _refusal(_lease(), _request(wall_time_used_seconds=-1.0)) is GateRefusal.MALFORMED_REQUEST
    )


# --- the sealed action policy --------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    ["EXECUTE_SHELL", "CLICK_SELECTOR", "NAVIGATE", "READ_CLIPBOARD", "DOWNLOAD", "next"],
)
def test_an_action_outside_the_sealed_policy_is_refused(action: str) -> None:
    """INV-01. Lowercase `next` is included because a case-insensitive comparison would be a
    plausible-looking way to accept anything the allowlist spells differently."""
    assert _refusal(_lease(), _request(action=action)) is GateRefusal.ACTION_NOT_ALLOWED


def test_a_key_chord_outside_the_platform_allowlist_is_refused() -> None:
    assert (
        _refusal(_lease(), _request(action="KEY_CHORD", key_chord="CMD+L"))
        is GateRefusal.KEY_CHORD_NOT_ALLOWED
    )


def test_a_chord_permitted_on_the_other_platform_is_refused_here() -> None:
    """`DOWN` is allowed for NVDA on Windows and is not a VoiceOver navigation chord."""
    assert (
        _refusal(_lease(), _request(action="KEY_CHORD", key_chord="DOWN", platform="darwin"))
        is GateRefusal.KEY_CHORD_NOT_ALLOWED
    )
    assert evaluate_action(
        _lease(), _request(action="KEY_CHORD", key_chord="DOWN", platform="win32")
    ).admitted


def test_an_unknown_platform_has_an_empty_chord_allowlist() -> None:
    """Fails closed. A missing platform key must not mean "no restrictions"."""
    assert (
        _refusal(_lease(), _request(action="KEY_CHORD", key_chord="TAB", platform="plan9"))
        is GateRefusal.KEY_CHORD_NOT_ALLOWED
    )


def test_a_key_chord_without_a_chord_is_malformed() -> None:
    assert _refusal(_lease(), _request(action="KEY_CHORD")) is GateRefusal.MALFORMED_REQUEST


def test_type_text_without_text_is_malformed() -> None:
    assert _refusal(_lease(), _request(action="TYPE_TEXT")) is GateRefusal.MALFORMED_REQUEST


# --- origin policy -------------------------------------------------------------------------------


def test_an_action_on_an_unpermitted_origin_is_refused() -> None:
    """The origin checked is the one observed, not the one intended; a navigation that went
    somewhere unexpected is exactly when the next keystroke must not be typed."""
    assert (
        _refusal(_lease(), _request(origin="https://mail.example.com"))
        is GateRefusal.ORIGIN_NOT_PERMITTED
    )


def test_stop_remains_available_on_an_unpermitted_origin() -> None:
    """Otherwise the supervisor is stranded on the page it most needs to stop on."""
    assert evaluate_action(
        _lease(), _request(action="STOP", origin="https://mail.example.com")
    ).admitted


# --- the decision type ---------------------------------------------------------------------------


def test_a_refusal_must_say_why() -> None:
    from accessforge_domain.runners.gate import ActionDecision

    with pytest.raises(ValueError, match="must say why"):
        ActionDecision(admitted=False)


def test_an_admitted_decision_cannot_carry_a_refusal() -> None:
    from accessforge_domain.runners.gate import ActionDecision

    with pytest.raises(ValueError, match="has no refusal"):
        ActionDecision(admitted=True, refusal=GateRefusal.LEASE_EXPIRED)


def test_the_gate_returns_a_decision_rather_than_raising() -> None:
    """Structural: a caller cannot admit an action by forgetting to wrap it in a try block."""
    decision = evaluate_action(_lease(cancel_requested=True), _request())
    assert decision.admitted is False


def test_the_audit_timestamp_is_still_required_to_be_real_utc() -> None:
    """Monotonic deadlines do not excuse a meaningless audit timestamp."""
    assert_audit_timestamp("2026-09-10T12:00:00.000000Z")
    with pytest.raises(TimestampError):
        assert_audit_timestamp("1500.25")
