"""Physical desktop identity and preflight as evidence.

The negative cases from the module prompt that live at this layer: "profile lies" (a runner claiming
a reader version it does not have), and the structural one underneath everything else — that session
identity must name a desktop rather than a process, because a process identity admits two attempts
to one screen.

Requirements: FR-004, FR-014. Invariants: INV-02, INV-03, INV-10.
"""

from __future__ import annotations

import pytest

from accessforge_domain.runners import (
    REQUIRED_PREFLIGHT_CHECKS,
    EnrollmentError,
    PhysicalSession,
    PreflightCheck,
    PreflightError,
    PreflightResult,
    RunnerProfile,
    admissible_runner_transitions,
    assert_runner_transition,
    profile_digest,
)
from accessforge_domain.runners.identity import assert_not_process_identity
from accessforge_domain.states import Condition, RunnerStatus

PROFILE = RunnerProfile(
    platform="darwin",
    reader_name="VoiceOver",
    reader_version="10.0",
    browser_name="Safari",
    browser_version="18.2",
    locale="en-US",
    keyboard_layout="ANSI",
)


def _session(**overrides: object) -> PhysicalSession:
    fields: dict[str, object] = {
        "device_id": "desk-01",
        "platform": "darwin",
        "interactive_session_id": "100005",
        "console": True,
    }
    fields.update(overrides)
    return PhysicalSession(**fields)  # type: ignore[arg-type]


def _checks(**overrides: Condition) -> dict[PreflightCheck, Condition]:
    checks = dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, Condition.TRUE)
    for name, value in overrides.items():
        checks[PreflightCheck(name)] = value
    return checks


def _result(**overrides: object) -> PreflightResult:
    fields: dict[str, object] = {
        "runner_profile_digest": PROFILE.digest,
        "environment_config_digest": "a" * 64,
        "manifest_digest": "b" * 64,
        "observed_reader_version": "10.0",
        "observed_browser_version": "18.2",
        "observed_locale": "en-US",
        "observed_keyboard_layout": "ANSI",
        "desktop_session_key": _session().key,
        "observed_at": "2026-09-10T12:00:00.000000Z",
        "checks": _checks(),
    }
    fields.update(overrides)
    return PreflightResult(**fields)  # type: ignore[arg-type]


# --- physical session identity -------------------------------------------------------------------


def test_the_same_desktop_produces_the_same_key() -> None:
    assert _session().key == _session().key


def test_a_different_interactive_session_on_one_machine_is_a_different_desktop() -> None:
    """Two people signed in to one Mac have two screens, and neither should see the other typing."""
    assert _session().key != _session(interactive_session_id="100006").key


def test_the_same_session_number_on_two_machines_is_two_desktops() -> None:
    """Windows session id 1 exists on every host; without the device the key would collide."""
    assert (
        _session(platform="win32", interactive_session_id="1").key
        != _session(platform="win32", interactive_session_id="1", device_id="desk-02").key
    )


def test_a_remote_session_is_not_the_console_session() -> None:
    """Same machine, same session number, different screen. The flag is part of the key."""
    assert _session(console=True).key != _session(console=False).key


def test_an_unsupported_platform_is_refused() -> None:
    with pytest.raises(EnrollmentError, match="not a supported interactive desktop"):
        _session(platform="linux")


def test_an_empty_session_identifier_is_refused() -> None:
    with pytest.raises(EnrollmentError, match="not a usable identifier"):
        _session(interactive_session_id="")


@pytest.mark.parametrize(
    "offered",
    [
        {"pid": 4711},
        {"container_id": "9f2c"},
        {"hostname": "runner-a"},
        {"task_arn": "arn:aws:ecs:..."},
        {"boot_id": "abc"},
    ],
)
def test_a_process_identity_offered_as_session_identity_is_refused(
    offered: dict[str, object],
) -> None:
    """The mistake this guard exists for type-checks perfectly.

    Plumbing a container id into the session field produces a valid string, a valid digest and two
    admitted attempts on one screen.
    """
    with pytest.raises(EnrollmentError, match="name a process or a host"):
        assert_not_process_identity(offered)


def test_a_genuine_desktop_identity_is_accepted() -> None:
    """Allowed-path control: the guard is not simply refusing every dictionary."""
    assert_not_process_identity(
        {"device_id": "desk-01", "interactive_session_id": "100005", "console": True}
    )


# --- profile digests -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "reader_version",
        "browser_version",
        "locale",
        "keyboard_layout",
        "reader_name",
        "browser_name",
    ],
)
def test_every_profile_field_changes_the_digest(field: str) -> None:
    """INV-03. A locale change alters announcements; a layout change alters which chords arrive."""
    from dataclasses import replace

    assert profile_digest(replace(PROFILE, **{field: "changed"})) != PROFILE.digest


def test_an_empty_profile_field_is_refused() -> None:
    with pytest.raises(EnrollmentError, match="cannot be matched"):
        RunnerProfile(
            platform="darwin",
            reader_name="VoiceOver",
            reader_version="",
            browser_name="Safari",
            browser_version="18.2",
            locale="en-US",
            keyboard_layout="ANSI",
        )


# --- preflight as evidence -----------------------------------------------------------------------


def test_a_complete_true_preflight_succeeds() -> None:
    assert _result().is_successful()


def test_a_single_false_check_is_enough_to_fail() -> None:
    result = _result(checks=_checks(SCREEN_UNLOCKED=Condition.FALSE))
    assert not result.is_successful()
    assert "SCREEN_UNLOCKED" in result.refusal_summary()


def test_an_unknown_check_is_not_a_pass() -> None:
    """INV-02's shape applied to readiness: not knowing is not the same as being fine."""
    result = _result(checks=_checks(NO_STALE_INPUT_SOURCE=Condition.UNKNOWN))
    assert not result.is_successful()
    assert "could not be determined" in result.refusal_summary()


def test_an_omitted_check_is_not_a_pass() -> None:
    """A supervisor that stops reporting a check after an upgrade must fail, not quietly pass."""
    checks = _checks()
    del checks[PreflightCheck.NO_STALE_INPUT_SOURCE]
    result = _result(checks=checks)
    assert not result.is_successful()
    assert PreflightCheck.NO_STALE_INPUT_SOURCE in result.missing_checks
    assert "not reported at all" in result.refusal_summary()


def test_every_required_check_is_individually_load_bearing() -> None:
    """No required check is decorative: dropping any one of them fails readiness."""
    for check in REQUIRED_PREFLIGHT_CHECKS:
        checks = _checks()
        del checks[check]
        assert not _result(checks=checks).is_successful(), f"{check} was not required after all"


def test_there_is_no_field_a_runner_can_set_to_declare_itself_ready() -> None:
    """Structural. A `ready` field would be one careless assignment away from a self-assertion."""
    fields = set(PreflightResult.__dataclass_fields__)
    assert not any(
        name in fields for name in ("ready", "is_ready", "successful", "status", "healthy")
    ), f"PreflightResult gained a self-assertion field: {sorted(fields)}"


def test_a_check_outside_the_closed_vocabulary_is_refused() -> None:
    with pytest.raises(PreflightError, match="outside the closed vocabulary"):
        PreflightResult(
            runner_profile_digest=PROFILE.digest,
            environment_config_digest="a" * 64,
            manifest_digest="b" * 64,
            observed_reader_version="10.0",
            observed_browser_version="18.2",
            observed_locale="en-US",
            observed_keyboard_layout="ANSI",
            desktop_session_key=_session().key,
            observed_at="2026-09-10T12:00:00.000000Z",
            checks={"EVERYTHING_IS_FINE": Condition.TRUE},  # type: ignore[dict-item]
        )


def test_a_preflight_must_name_the_environment_it_is_evidence_about() -> None:
    with pytest.raises(PreflightError, match="evidence about nothing in particular"):
        _result(manifest_digest="  ")


def test_a_successful_preflight_has_no_refusal_summary() -> None:
    assert _result().refusal_summary() == ""


# --- runner transitions --------------------------------------------------------------------------


def test_ready_requires_a_preflight_result() -> None:
    with pytest.raises(PreflightError, match="cannot report itself ready"):
        assert_runner_transition(RunnerStatus.PREFLIGHT_REQUIRED, RunnerStatus.READY)


def test_ready_requires_a_successful_preflight_result() -> None:
    with pytest.raises(PreflightError, match="did not establish readiness"):
        assert_runner_transition(
            RunnerStatus.PREFLIGHT_REQUIRED,
            RunnerStatus.READY,
            preflight=_result(checks=_checks(READER_ACTIVE=Condition.FALSE)),
        )


def test_ready_is_reachable_with_a_successful_preflight() -> None:
    assert_runner_transition(
        RunnerStatus.PREFLIGHT_REQUIRED, RunnerStatus.READY, preflight=_result()
    )


def test_quarantine_leads_only_back_to_preflight() -> None:
    """Not to READY and certainly not to BUSY. A fenced desktop re-proves itself or stays fenced."""
    assert admissible_runner_transitions(RunnerStatus.QUARANTINED) == frozenset(
        {RunnerStatus.PREFLIGHT_REQUIRED}
    )


def test_a_quarantined_runner_cannot_become_ready_even_with_a_good_preflight() -> None:
    """The reset has to happen first. A preflight on a desktop that may still have a live actor
    typing into it is a preflight of a lie."""
    with pytest.raises(PreflightError, match="cannot become"):
        assert_runner_transition(RunnerStatus.QUARANTINED, RunnerStatus.READY, preflight=_result())


def test_a_quarantined_runner_cannot_be_leased() -> None:
    assert RunnerStatus.BUSY not in admissible_runner_transitions(RunnerStatus.QUARANTINED)


def test_every_status_can_be_quarantined() -> None:
    """An ambiguous action or an expired lease is discovered, not scheduled."""
    for status in RunnerStatus:
        if status is RunnerStatus.QUARANTINED:
            continue
        assert RunnerStatus.QUARANTINED in admissible_runner_transitions(status), status


def test_a_busy_runner_does_not_return_directly_to_ready() -> None:
    """The browser is wherever the finished run left it, so readiness must be re-established."""
    assert RunnerStatus.READY not in admissible_runner_transitions(RunnerStatus.BUSY)
