"""Everything rechecked at the moment of dispatch.

"A standing grant alone cannot dispatch a run." The negative cases from the module prompt that live
here: a revoked grant's child dispatches; the parent grant moved since minting; the runner profile
changed; the preflight belongs to an earlier session; the lease was cancelled or released.

Requirements: FR-005, FR-014, FR-021. Invariants: INV-03, INV-06, INV-08, INV-14.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace

import pytest

from accessforge_domain.authority import ChildAuthorization, ExecutionGrant
from accessforge_domain.canonical import digest
from accessforge_domain.runners import (
    REQUIRED_PREFLIGHT_CHECKS,
    PhysicalSession,
    PreflightCheck,
    PreflightResult,
    RunnerProfile,
)
from accessforge_domain.states import ApprovalScope, Condition
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    runners,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x17))
OPERATOR = str(uuid.UUID(int=0x1A))
PROJECT = str(uuid.UUID(int=0x1B))
JOURNEY_VERSION = str(uuid.UUID(int=0x1C))
POLICY_VERSION = str(uuid.UUID(int=0x1D))
GRANT = str(uuid.UUID(int=0x1E))

MANIFEST = digest({"m": "07-dispatch"})
ENVIRONMENT = digest({"env": "local"})
NOW = "2026-09-10T12:00:00.000000Z"
LATER = "2026-09-10T12:01:00.000000Z"

PROFILE = RunnerProfile(
    platform="darwin",
    reader_name="VoiceOver",
    reader_version="10.0",
    browser_name="Safari",
    browser_version="18.2",
    locale="en-US",
    keyboard_layout="ANSI",
)
SESSION = PhysicalSession(
    device_id="desk-07", platform="darwin", interactive_session_id="100011", console=True
)


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (WS, "A"))
    yield test_database_url


def _grant(**overrides: object) -> ExecutionGrant:
    fields: dict[str, object] = {
        "grant_id": GRANT,
        "workspace_id": WS,
        "project_id": PROJECT,
        "environment": "local",
        "allowed_journey_version_ids": frozenset({JOURNEY_VERSION}),
        "allowed_policy_version_ids": frozenset({POLICY_VERSION}),
        "permitted_effects": frozenset({"CREATE_TEST_REQUEST"}),
        "action_budget": 100,
        "wall_time_budget_seconds": 900,
        "expires_at": "2026-09-11T12:00:00.000000Z",
        "revision": 3,
    }
    fields.update(overrides)
    return ExecutionGrant(**fields)  # type: ignore[arg-type]


def _child(run_id: str, **overrides: object) -> ChildAuthorization:
    fields: dict[str, object] = {
        "authorization_id": str(uuid.uuid4()),
        "run_id": run_id,
        "workspace_id": WS,
        "project_id": PROJECT,
        "journey_version_id": JOURNEY_VERSION,
        "policy_version_id": POLICY_VERSION,
        "permitted_effects": frozenset({"CREATE_TEST_REQUEST"}),
        "action_budget": 50,
        "wall_time_budget_seconds": 600,
        "expires_at": "2026-09-10T13:00:00.000000Z",
        "parent_grant_id": GRANT,
        "parent_grant_revision": 3,
        "issuing_service_identity": "orchestrator",
    }
    fields.update(overrides)
    return ChildAuthorization(**fields)  # type: ignore[arg-type]


def _checks(**overrides: Condition) -> dict[PreflightCheck, Condition]:
    checks = dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, Condition.TRUE)
    for name, value in overrides.items():
        checks[PreflightCheck(name)] = value
    return checks


def _preflight(
    *,
    profile: RunnerProfile = PROFILE,
    manifest: str = MANIFEST,
    environment: str = ENVIRONMENT,
    checks: dict[PreflightCheck, Condition] | None = None,
) -> PreflightResult:
    return PreflightResult(
        runner_profile_digest=profile.digest,
        environment_config_digest=environment,
        manifest_digest=manifest,
        observed_reader_version=profile.reader_version,
        observed_browser_version=profile.browser_version,
        observed_locale=profile.locale,
        observed_keyboard_layout=profile.keyboard_layout,
        desktop_session_key=SESSION.key,
        observed_at=NOW,
        checks=checks if checks is not None else _checks(),
    )


def _leased(db: str) -> tuple[str, str, str, int]:
    """Enroll, preflight, create a run and admit a lease. Returns the whole set."""
    with workspace_connection(db, WS) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=WS, created_by=OPERATOR)
        enrolled = runners.enroll_runner(
            conn,
            workspace_id=WS,
            token=token.token,
            name="mac-07",
            session=SESSION,
            profile=PROFILE,
        )
        runners.record_preflight(
            conn, workspace_id=WS, runner_id=enrolled.runner_id, result=_preflight()
        )
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        lease = runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=enrolled.runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            now=NOW,
        )
    return enrolled.runner_id, lease.lease_id, run_id, lease.epoch


def _authorize(
    db: str,
    runner_id: str,
    lease_id: str,
    epoch: int,
    run_id: str,
    *,
    child: ChildAuthorization | None = None,
    parent: ExecutionGrant | None = None,
    profile_digest: str | None = None,
    environment: str = ENVIRONMENT,
    manifest: str = MANIFEST,
) -> None:
    with workspace_connection(db, WS) as conn:
        runners.assert_dispatch_authorized(
            conn,
            runner_id=runner_id,
            lease_id=lease_id,
            epoch=epoch,
            child=child or _child(run_id),
            parent=parent or _grant(),
            workspace_id=WS,
            run_id=run_id,
            expected_profile_digest=profile_digest or PROFILE.digest,
            expected_environment_config_digest=environment,
            expected_manifest_digest=manifest,
            now=LATER,
        )


# --- the allowed path ----------------------------------------------------------------------------


def test_a_fully_authorized_dispatch_is_permitted(db: str) -> None:
    """Control. Without it every refusal below could be a function that refuses everything."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    _authorize(db, runner_id, lease_id, epoch, run_id)


# --- authority -----------------------------------------------------------------------------------


def test_a_revoked_parent_grant_refuses_its_own_child(db: str) -> None:
    """The child is unchanged and still unexpired. Its parent is gone, so nobody authorized this."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="revoked"):
        _authorize(db, runner_id, lease_id, epoch, run_id, parent=_grant(revoked=True))


def test_a_parent_grant_revised_since_minting_refuses(db: str) -> None:
    """Someone edited the standing grant between approval and dispatch."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="revision"):
        _authorize(db, runner_id, lease_id, epoch, run_id, parent=_grant(revision=4))


def test_an_expired_child_authorization_refuses(db: str) -> None:
    runner_id, lease_id, run_id, epoch = _leased(db)
    expired = _child(run_id, expires_at="2026-09-10T11:00:00.000000Z")
    with pytest.raises(runners.DispatchRefused, match="expired"):
        _authorize(db, runner_id, lease_id, epoch, run_id, child=expired)


def test_a_child_bound_to_a_different_run_refuses(db: str) -> None:
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="different run"):
        _authorize(db, runner_id, lease_id, epoch, run_id, child=_child(str(uuid.uuid4())))


def test_a_forged_child_exceeding_its_grant_refuses(db: str) -> None:
    """Containment is re-verified, not assumed from minting. A child is a data structure."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    forged = _child(
        run_id,
        permitted_effects=frozenset({"CREATE_TEST_REQUEST", "SEND_EMAIL"}),
        action_budget=999999,
    )
    with pytest.raises(runners.DispatchRefused):
        _authorize(db, runner_id, lease_id, epoch, run_id, child=forged)


def test_a_patch_scope_authorization_cannot_execute_a_run(db: str) -> None:
    """Scopes do not nest. PATCH_APPLY authorizes an isolated candidate workspace, only."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="not RUN_EFFECTS"):
        _authorize(
            db,
            runner_id,
            lease_id,
            epoch,
            run_id,
            child=_child(run_id, scope=ApprovalScope.PATCH_APPLY),
        )


def test_a_zero_budget_is_not_an_unlimited_one(db: str) -> None:
    """INV-14. A missing budget must fail closed, and closed means refused."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="no usable budget"):
        _authorize(db, runner_id, lease_id, epoch, run_id, child=_child(run_id, action_budget=0))


# --- the desktop ---------------------------------------------------------------------------------


def test_a_quarantined_desktop_refuses_dispatch(db: str) -> None:
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=str(
                conn.execute(
                    "SELECT attempt_id FROM desktop_lease WHERE id = %s", (lease_id,)
                ).fetchone()["attempt_id"]
            ),
            epoch=epoch,
            action_sequence=1,
            action="NEXT",
            origin="http://127.0.0.1:8081",
        )
    with workspace_connection(db, WS) as conn:
        action = conn.execute(
            "SELECT id FROM runner_action WHERE lease_id = %s", (lease_id,)
        ).fetchone()
        runners.mark_action_ambiguous(
            conn,
            action_id=str(action["id"]),
            reason=runners.AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED,
        )
    with pytest.raises(runners.DispatchRefused, match="quarantined"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


def test_a_changed_runner_profile_refuses_dispatch(db: str) -> None:
    """INV-03. A reader upgrade between approval and dispatch changes what the run means."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    upgraded = replace(PROFILE, reader_version="10.1")
    with pytest.raises(runners.DispatchRefused, match="runner profile has changed"):
        _authorize(db, runner_id, lease_id, epoch, run_id, profile_digest=upgraded.digest)


def test_a_revoked_runner_refuses_dispatch(db: str) -> None:
    """The lease is stopped first, because a runner holding one is not revocable.

    The first version of this test revoked straight through an active lease, and the guard added for
    exactly that case refused it -- correctly. Retiring a machine mid-run is the operator's route
    here: acknowledge the stop, then revoke.
    """
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        runners.request_cancellation(conn, lease_id=lease_id, cancellation_revision=1)
        runners.acknowledge_stop(conn, lease_id=lease_id, epoch=epoch)
        runners.revoke_runner(conn, runner_id=runner_id)
    with pytest.raises(runners.DispatchRefused, match="revoked"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


# --- the lease -----------------------------------------------------------------------------------


def test_a_stale_epoch_refuses_dispatch(db: str) -> None:
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="claims epoch"):
        _authorize(db, runner_id, lease_id, epoch + 1, run_id)


def test_a_cancelled_lease_refuses_new_work(db: str) -> None:
    """INV-13, checked here as well as in the action gate: cancellation fences at every layer."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        runners.request_cancellation(conn, lease_id=lease_id, cancellation_revision=1)
    with pytest.raises(runners.DispatchRefused, match="cancellation has been requested"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


def test_a_released_lease_is_not_a_standing_right_to_act(db: str) -> None:
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        runners.request_cancellation(conn, lease_id=lease_id, cancellation_revision=1)
        runners.acknowledge_stop(conn, lease_id=lease_id, epoch=epoch)
    with pytest.raises(runners.DispatchRefused, match="already released"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


# --- preflight binding ---------------------------------------------------------------------------


def test_a_runner_with_no_preflight_fails_closed(db: str) -> None:
    """Missing prerequisites fail closed. An unproven desktop is not a ready one."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        conn.execute("DELETE FROM runner_preflight WHERE runner_id = %s", (runner_id,))
    with pytest.raises(runners.DispatchRefused, match="never submitted a preflight"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


def test_a_failed_preflight_quarantines_before_dispatch_is_even_reached(db: str) -> None:
    """Recording a failure fences the desktop, so the quarantine is what a dispatch meets first.

    That ordering is correct -- quarantine is the more fundamental fact -- and it is asserted here
    rather than worked around, because the first version of this test expected the readiness message
    and would have been satisfied by a version of the code that had stopped quarantining.
    """
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        runners.record_preflight(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            result=_preflight(checks=_checks(SCREEN_UNLOCKED=Condition.FALSE)),
            now=LATER,
        )
    with pytest.raises(runners.DispatchRefused, match="quarantined"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


def test_the_most_recent_preflight_is_the_one_that_counts(db: str) -> None:
    """Not any successful one ever recorded. A later failure supersedes an earlier success.

    The row is written directly so the runner is not also quarantined: the question under test is
    which preflight the dispatch check reads, and a quarantine would answer a different one.
    """
    runner_id, lease_id, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        conn.execute(
            """
            INSERT INTO runner_preflight
                (id, workspace_id, runner_id, lease_epoch, runner_profile_digest,
                 environment_config_digest, manifest_digest, successful, refusal_summary, checks,
                 observed, observed_at, recorded_at)
            VALUES (%s, %s, %s, 0, %s, %s, %s, false, %s, '{}', '{}', %s, %s)
            """,
            (
                str(uuid.uuid4()),
                WS,
                runner_id,
                PROFILE.digest,
                ENVIRONMENT,
                MANIFEST,
                "failed: SCREEN_UNLOCKED",
                LATER,
                LATER,
            ),
        )
    with pytest.raises(runners.DispatchRefused, match="did not establish readiness"):
        _authorize(db, runner_id, lease_id, epoch, run_id)


def test_a_preflight_from_an_earlier_session_does_not_vouch_for_this_one(db: str) -> None:
    """A lease came and went between that preflight and this one.

    The desktop is walked to READY without a fresh preflight -- the shape a bug or a bypass would
    take -- and a second lease is admitted legitimately. The authorization, the profile, the lease
    and the epoch are all in order; the only thing wrong is that the readiness evidence describes a
    session that has since ended, and that alone must refuse the dispatch.
    """
    runner_id, first_lease, run_id, epoch = _leased(db)
    with workspace_connection(db, WS) as conn:
        runners.request_cancellation(conn, lease_id=first_lease, cancellation_revision=1)
        runners.acknowledge_stop(conn, lease_id=first_lease, epoch=epoch)
        conn.execute("UPDATE runner SET status = 'READY' WHERE id = %s", (runner_id,))
        second_run = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        second_attempt = runs.start_attempt(conn, run_id=second_run, workspace_id=WS, lease_epoch=2)
        second = runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=second_run,
            attempt_id=second_attempt,
            now=NOW,
        )
    assert second.epoch == 2

    with pytest.raises(runners.DispatchRefused, match="previous session"):
        _authorize(db, runner_id, second.lease_id, second.epoch, second_run)


def test_a_preflight_against_a_different_manifest_refuses(db: str) -> None:
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="different sealed manifest"):
        _authorize(db, runner_id, lease_id, epoch, run_id, manifest=digest({"m": "something-else"}))


def test_a_preflight_against_a_different_environment_refuses(db: str) -> None:
    """The permitted origins and reset strategy it verified are not this run's."""
    runner_id, lease_id, run_id, epoch = _leased(db)
    with pytest.raises(runners.DispatchRefused, match="different environment configuration"):
        _authorize(db, runner_id, lease_id, epoch, run_id, environment=digest({"env": "staging"}))
