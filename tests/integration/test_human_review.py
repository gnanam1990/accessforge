"""Human review, the finding lifecycle, and what a review is not allowed to become.

The negative cases the module prompt lists: cross-workspace access, wrong role, stale digest, an
accepted review of an INCONCLUSIVE candidate, duplicate submission, concurrent reviewers, and
append-only corrections. Plus the two proofs it asks for by name — that a review cannot mutate
`Run.outcome` and cannot issue an execution approval.

Requirements: FR-011, FR-012. Invariants: INV-11, INV-12, INV-16.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from accessforge_domain import reducers
from accessforge_domain.authorization.roles import Permission, Role, role_permits
from accessforge_domain.canonical import digest
from accessforge_domain.states import FindingStatus, Outcome, ReviewVerdict
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    projects,
    reviews,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xC0DE))
WS_OTHER = str(uuid.UUID(int=0xC0DF))
AUTHOR = str(uuid.UUID(int=0x11))
REVIEWER = str(uuid.UUID(int=0x22))
SECOND_REVIEWER = str(uuid.UUID(int=0x33))

MANIFEST = digest({"m": "16"})
PATCH = digest({"patch": "add-aria-describedby"})
VERIFICATION = digest({"verification": "candidate-passed"})
ENVIRONMENT = digest({"env": "local"})


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


def _journey(url: str, workspace: str = WS) -> str:
    """A journey version to bind reviews to. Built with raw SQL: module 06 has no writer yet."""
    with workspace_connection(url, workspace) as conn:
        project_id = projects.create_project(conn, workspace_id=workspace, name="p")
        version_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO journey_version
                (id, workspace_id, project_id, name, platform, journey_digest,
                 assertion_set_digest, fixture_digest, navigator_policy_digest, navigator_policy,
                 reviewer_summary)
            VALUES (%s, %s, %s, 'j', 'darwin', %s, %s, %s, %s, '{}', '{}')
            """,
            (version_id, workspace, project_id, MANIFEST, MANIFEST, MANIFEST, MANIFEST),
        )
    return version_id


def _submission(
    verdict: ReviewVerdict = ReviewVerdict.ACCEPT,
    *,
    limitations: str = "did not test with a braille display",
    used_at: bool = True,
) -> reviews.ReviewSubmission:
    return reviews.ReviewSubmission(
        verdict=verdict,
        observations="Drove the repaired form with VoiceOver; the error is now announced on focus.",
        limitations=limitations,
        used_assistive_technology=used_at,
        assistive_technology_detail="VoiceOver on macOS 26.6" if used_at else None,
    )


def _submit(
    url: str,
    journey_version_id: str,
    *,
    workspace: str = WS,
    reviewer_id: str = REVIEWER,
    reviewer_role: Role = Role.REVIEWER,
    submission: reviews.ReviewSubmission | None = None,
    patch: str = PATCH,
    verification: str = VERIFICATION,
    current_patch: str = PATCH,
    current_verification: str = VERIFICATION,
    author: str | None = AUTHOR,
    supersedes: str | None = None,
) -> str:
    with workspace_connection(url, workspace) as conn:
        return reviews.submit_review(
            conn,
            workspace_id=workspace,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            patch_digest=patch,
            verification_digest=verification,
            journey_version_id=journey_version_id,
            environment_digest=ENVIRONMENT,
            submission=submission or _submission(),
            current_patch_digest=current_patch,
            current_verification_digest=current_verification,
            patch_author_id=author,
            supersedes=supersedes,
        )


# --- the allowed path ----------------------------------------------------------------------------


def test_an_authorized_reviewer_can_record_an_assessment(db: str) -> None:
    """The control. Every refusal below would be meaningless against a function that refuses all."""
    journey = _journey(db)
    review_id = _submit(db, journey)
    with workspace_connection(db, WS) as conn:
        history = reviews.review_history(conn, patch_digest=PATCH)
    assert [str(r["id"]) for r in history] == [review_id]
    assert str(history[0]["verdict"]) == ReviewVerdict.ACCEPT


@pytest.mark.parametrize(
    "verdict",
    [ReviewVerdict.ACCEPT, ReviewVerdict.CHANGES_REQUESTED, ReviewVerdict.UNABLE_TO_ASSESS],
)
def test_all_three_verdicts_are_recordable(db: str, verdict: ReviewVerdict) -> None:
    """UNABLE_TO_ASSESS especially. An absent reviewer, an unsupported environment or an interrupted
    review must have somewhere to go that is not acceptance."""
    journey = _journey(db)
    _submit(db, journey, submission=_submission(verdict))


def test_unable_to_assess_must_say_what_was_missing(db: str) -> None:
    """Otherwise it records that a person's time was spent and nothing about why they could not
    answer — the least useful row in the system."""
    with pytest.raises(reviews.ReviewError, match="must say what was missing"):
        _submission(ReviewVerdict.UNABLE_TO_ASSESS, limitations="   ")


def test_a_review_must_say_what_was_observed(db: str) -> None:
    with pytest.raises(reviews.ReviewError, match="what the reviewer observed"):
        reviews.ReviewSubmission(
            verdict=ReviewVerdict.ACCEPT,
            observations="  ",
            limitations="",
            used_assistive_technology=False,
        )


# --- what a review is not ------------------------------------------------------------------------


def test_a_review_cannot_change_the_run_outcome(db: str) -> None:
    """Asked for by name in the prompt, and asserted by observing the run before and after.

    A structural check would be weaker: the point is not that no column is named `outcome` here, but
    that recording an ACCEPT leaves what the evidence established untouched (INV-12).
    """
    journey = _journey(db)
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        before = conn.execute(
            "SELECT status, outcome, revision FROM run WHERE id = %s", (run_id,)
        ).fetchone()

    _submit(db, journey)

    with workspace_connection(db, WS) as conn:
        after = conn.execute(
            "SELECT status, outcome, revision FROM run WHERE id = %s", (run_id,)
        ).fetchone()
    assert before is not None
    assert after is not None
    assert dict(before) == dict(after)


def test_reviewing_confers_no_execution_authority(db: str) -> None:
    """ACCEPT is not RUN_EFFECTS, PATCH_APPLY, GITHUB_PUBLISH or a deployment approval.

    Read from module 03's matrix rather than restated: a second copy of this rule would be a second
    thing to keep in agreement, and the two would eventually disagree.
    """
    for forbidden in (
        Permission.RUN_APPROVE,
        Permission.PATCH_APPROVE,
        Permission.GITHUB_PUBLISH_APPROVE,
        Permission.INFRASTRUCTURE_OPERATE,
    ):
        assert not role_permits(Role.REVIEWER, forbidden), forbidden
    assert role_permits(Role.REVIEWER, Permission.PATCH_REVIEW)


def test_a_role_without_patch_review_cannot_submit(db: str) -> None:
    journey = _journey(db)
    with pytest.raises(reviews.ReviewError, match="does not hold"):
        _submit(db, journey, reviewer_role=Role.VIEWER)


def test_the_reviewer_role_set_is_derived_from_the_permission_matrix(db: str) -> None:
    """Not a hand-written list that could fall behind a matrix change."""
    assert reviews.REVIEWER_ROLES == frozenset(
        r for r in Role if role_permits(r, Permission.PATCH_REVIEW)
    )


# --- stale context -------------------------------------------------------------------------------


def test_a_review_of_a_superseded_patch_is_refused(db: str) -> None:
    """The bytes changed after the reviewer looked. Carrying their acceptance forward would
    attribute an opinion about other content to them."""
    journey = _journey(db)
    with pytest.raises(reviews.StaleReviewContext, match="bytes changed after the reviewer looked"):
        _submit(db, journey, current_patch=digest({"patch": "something-else"}))


def test_a_review_whose_verification_moved_is_refused(db: str) -> None:
    journey = _journey(db)
    with pytest.raises(reviews.StaleReviewContext, match="proof no longer holds"):
        _submit(db, journey, current_verification=digest({"verification": "now-failing"}))


def test_the_staleness_check_compares_two_different_sources(db: str) -> None:
    """Structural, and the reason the signature has separate `current_*` parameters.

    Comparing a stored record to itself always agrees, so a single-source check would pass
    unconditionally while looking like protection.
    """
    import inspect

    params = set(inspect.signature(reviews.submit_review).parameters)
    assert {"patch_digest", "current_patch_digest"} <= params
    assert {"verification_digest", "current_verification_digest"} <= params


# --- independence --------------------------------------------------------------------------------


def test_an_author_cannot_review_their_own_patch(db: str) -> None:
    """A self-review satisfies the process and not the purpose."""
    journey = _journey(db)
    with pytest.raises(reviews.IndependencePolicyViolation, match="authored this patch"):
        _submit(db, journey, reviewer_id=AUTHOR)


def test_independence_is_enforced_on_canonical_identity(db: str) -> None:
    """A second session or an alias resolves to the same actor id.

    Enforced on anything a person controls — a display name, a session — the policy would be a
    suggestion. This test asserts the parameter that carries it is the identity, not a label.
    """
    journey = _journey(db)
    with pytest.raises(reviews.IndependencePolicyViolation):
        _submit(db, journey, reviewer_id=AUTHOR)
    # A different person with the same role is fine.
    _submit(db, journey, reviewer_id=SECOND_REVIEWER)


# --- append-only ---------------------------------------------------------------------------------


def test_a_review_cannot_be_edited_or_deleted(db: str) -> None:
    """INV-11. A correction is a new review that supersedes the old one."""
    journey = _journey(db)
    review_id = _submit(db, journey)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(psycopg.errors.IntegrityError, match="immutable"):
            conn.execute("UPDATE review SET verdict = 'ACCEPT' WHERE id = %s", (review_id,))
    with workspace_connection(db, WS) as conn:
        with pytest.raises(psycopg.errors.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM review WHERE id = %s", (review_id,))


def test_a_correction_supersedes_without_erasing(db: str) -> None:
    """What a reviewer said and then said instead is the history. Showing only the latest would
    present a changed mind as a first impression."""
    journey = _journey(db)
    first = _submit(db, journey, submission=_submission(ReviewVerdict.ACCEPT))
    second = _submit(
        db,
        journey,
        submission=_submission(
            ReviewVerdict.CHANGES_REQUESTED, limitations="found a regression on re-reading"
        ),
        supersedes=first,
    )

    with workspace_connection(db, WS) as conn:
        history = reviews.review_history(conn, patch_digest=PATCH)
        effective = reviews.effective_reviews(conn, patch_digest=PATCH)

    assert [str(r["id"]) for r in history] == [first, second], "both survive"
    assert [str(r["id"]) for r in effective] == [second], "only the correction is in force"


def test_two_reviewers_may_assess_the_same_patch(db: str) -> None:
    """Concurrent reviewers are two opinions, not a conflict."""
    journey = _journey(db)
    _submit(db, journey, reviewer_id=REVIEWER)
    _submit(db, journey, reviewer_id=SECOND_REVIEWER)
    with workspace_connection(db, WS) as conn:
        assert len(reviews.effective_reviews(conn, patch_digest=PATCH)) == 2


# --- no demographic or disability data ------------------------------------------------------------


def test_there_is_nowhere_to_record_demographic_or_disability_data(db: str) -> None:
    """Not an unused nullable column, not a JSONB blob that could hold one. A field that exists
    gets filled, and normal product use requires no such disclosure."""
    with unscoped_connection(db) as conn:
        columns = {
            str(r["column_name"]).lower()
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'review'"
            ).fetchall()
        }
    for forbidden in ("disability", "impairment", "demographic", "age", "gender", "ethnicity"):
        assert not any(forbidden in c for c in columns), f"{forbidden} has a home in `review`"
    assert "metadata" not in columns and "extra" not in columns, "no free-form blob either"


def test_assistive_technology_use_is_recorded_but_never_inferred(db: str) -> None:
    """ "A person accepted this" and "a person accepted this having driven it with a screen reader"
    are different claims. The second is recorded only when the reviewer says so."""
    journey = _journey(db)
    review_id = _submit(db, journey, submission=_submission(used_at=False))
    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            "SELECT used_assistive_technology, assistive_technology_detail FROM review "
            "WHERE id = %s",
            (review_id,),
        ).fetchone()
    assert row is not None
    assert row["used_assistive_technology"] is False
    assert row["assistive_technology_detail"] is None


def test_assistive_technology_detail_without_the_flag_is_refused(db: str) -> None:
    with pytest.raises(reviews.ReviewError, match="one of the two is wrong"):
        reviews.ReviewSubmission(
            verdict=ReviewVerdict.ACCEPT,
            observations="looked at it",
            limitations="",
            used_assistive_technology=False,
            assistive_technology_detail="VoiceOver",
        )


# --- the finding lifecycle ------------------------------------------------------------------------


def _finished_run(conn: psycopg.Connection[dict[str, object]], outcome: Outcome) -> str:
    """A run driven to a terminal state through the reducers.

    Not an UPDATE setting the outcome directly: the `run` table holds a check constraint pairing
    status with outcome, and a QUEUED run with a FAIL outcome is a state the schema refuses. The
    first version of this helper tried it and was told so, which is the constraint doing its job.
    """
    run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
    for _ in range(3):  # QUEUED -> LEASED -> RUNNING -> FINALIZING
        state = runs.load_run(conn, run_id=run_id).state
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: reducers.progress(s, expected_revision=s.revision),
            operation_id=str(uuid.uuid4()),
            topic="run.progress",
            expected_revision=state.revision,
            actor_service="test",
        )
    state = runs.load_run(conn, run_id=run_id).state
    runs.apply_transition(
        conn,
        run_id=run_id,
        reducer=lambda s: reducers.complete(s, outcome=outcome, expected_revision=s.revision),
        operation_id=str(uuid.uuid4()),
        topic="run.completed",
        expected_revision=state.revision,
        actor_service="test",
    )
    return run_id


def _finding(db: str, *, outcome: Outcome, status: FindingStatus) -> tuple[str, str]:
    with workspace_connection(db, WS) as conn:
        run_id = _finished_run(conn, outcome)
        finding_id = reviews.create_finding(
            conn,
            workspace_id=WS,
            run_id=run_id,
            assertion_id="announcement.validation-error",
            summary="the error is not announced",
            status=status,
            run_outcome=outcome,
            actor_id=REVIEWER,
        )
    return run_id, finding_id


def test_a_finding_cannot_open_as_reproduced_from_an_inconclusive_run(db: str) -> None:
    """The accepted-review-of-an-INCONCLUSIVE-candidate case, caught one step earlier.

    An inconclusive run frequently *contains* a real-looking failure, which is exactly why opening a
    confirmed defect from one is easy to do and wrong (INV-02).
    """
    with pytest.raises(reviews.ReviewError, match="cannot open as REPRODUCED"):
        _finding(db, outcome=Outcome.INCONCLUSIVE, status=FindingStatus.REPRODUCED)


def test_a_finding_opens_as_a_candidate_from_an_inconclusive_run(db: str) -> None:
    _, finding_id = _finding(db, outcome=Outcome.INCONCLUSIVE, status=FindingStatus.CANDIDATE)
    with workspace_connection(db, WS) as conn:
        assert len(reviews.finding_history(conn, finding_id=finding_id)) == 1


def test_resolved_requires_an_accepting_review(db: str) -> None:
    """ "RESOLVED requires a verified repair and required human review." A finding marked resolved
    with no review attached is indistinguishable from one nobody looked at."""
    journey = _journey(db)
    _, finding_id = _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)

    with workspace_connection(db, WS) as conn:
        with pytest.raises(reviews.ReviewError, match="requires the human review"):
            reviews.transition_finding(
                conn,
                workspace_id=WS,
                finding_id=finding_id,
                to_status=FindingStatus.RESOLVED,
                actor_id=REVIEWER,
                actor_role=Role.REVIEWER,
                reason="looks fixed",
                expected_revision=1,
            )

    changes_requested = _submit(
        db,
        journey,
        submission=_submission(ReviewVerdict.CHANGES_REQUESTED, limitations="still not announced"),
    )
    with workspace_connection(db, WS) as conn:
        with pytest.raises(reviews.ReviewError, match="not ACCEPT"):
            reviews.transition_finding(
                conn,
                workspace_id=WS,
                finding_id=finding_id,
                to_status=FindingStatus.RESOLVED,
                actor_id=REVIEWER,
                actor_role=Role.REVIEWER,
                reason="citing a review that asked for changes",
                expected_revision=1,
                review_id=changes_requested,
            )

    accepted = _submit(db, journey, reviewer_id=SECOND_REVIEWER)
    with workspace_connection(db, WS) as conn:
        reviews.transition_finding(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            to_status=FindingStatus.RESOLVED,
            actor_id=SECOND_REVIEWER,
            actor_role=Role.REVIEWER,
            reason="repair verified and reviewed",
            expected_revision=1,
            review_id=accepted,
        )
        history = reviews.finding_history(conn, finding_id=finding_id)
    assert history[-1]["to_status"] == FindingStatus.RESOLVED
    assert str(history[-1]["review_id"]) == accepted


def test_a_dismissal_needs_a_reason_and_is_recorded(db: str) -> None:
    _, finding_id = _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(reviews.ReviewError, match="needs a reason"):
            reviews.transition_finding(
                conn,
                workspace_id=WS,
                finding_id=finding_id,
                to_status=FindingStatus.DISMISSED,
                actor_id=REVIEWER,
                actor_role=Role.REVIEWER,
                reason="  ",
                expected_revision=1,
            )
        reviews.transition_finding(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            to_status=FindingStatus.DISMISSED,
            actor_id=REVIEWER,
            actor_role=Role.REVIEWER,
            reason="duplicate of an earlier finding on the same assertion",
            expected_revision=1,
        )
        history = reviews.finding_history(conn, finding_id=finding_id)
    assert history[-1]["reason"].startswith("duplicate of")
    assert str(history[-1]["actor_id"]) == REVIEWER


def test_a_dismissed_finding_can_be_reopened(db: str) -> None:
    """A dismissal is a judgement about a finding, not a deletion of it."""
    _, finding_id = _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)
    with workspace_connection(db, WS) as conn:
        reviews.transition_finding(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            to_status=FindingStatus.DISMISSED,
            actor_id=REVIEWER,
            actor_role=Role.REVIEWER,
            reason="thought it was a duplicate",
            expected_revision=1,
        )
        reviews.transition_finding(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            to_status=FindingStatus.REPRODUCED,
            actor_id=SECOND_REVIEWER,
            actor_role=Role.REVIEWER,
            reason="not a duplicate; different assertion",
            expected_revision=2,
        )
        history = reviews.finding_history(conn, finding_id=finding_id)
    assert [h["to_status"] for h in history] == ["REPRODUCED", "DISMISSED", "REPRODUCED"]


def test_a_stale_revision_refuses_the_transition(db: str) -> None:
    _, finding_id = _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(reviews.ReviewError, match="no longer exists"):
            reviews.transition_finding(
                conn,
                workspace_id=WS,
                finding_id=finding_id,
                to_status=FindingStatus.DISMISSED,
                actor_id=REVIEWER,
                actor_role=Role.REVIEWER,
                reason="x",
                expected_revision=99,
            )


# --- separate attribution -------------------------------------------------------------------------


def test_the_serialized_view_keeps_opinion_and_outcome_apart(db: str) -> None:
    """INV-12. A view that flattened them would let a reviewer's ACCEPT read as the system having
    verified something."""
    journey = _journey(db)
    _, finding_id = _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)
    accepted = _submit(db, journey, reviewer_id=SECOND_REVIEWER)
    with workspace_connection(db, WS) as conn:
        reviews.transition_finding(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            to_status=FindingStatus.RESOLVED,
            actor_id=SECOND_REVIEWER,
            actor_role=Role.REVIEWER,
            reason="reviewed",
            expected_revision=1,
            review_id=accepted,
        )
        view = reviews.serialize_for_display(conn, finding_id=finding_id)

    assert set(view) >= {"machineOutcome", "humanAssessments"}
    assert view["machineOutcome"]["runOutcome"] == Outcome.FAIL
    assert view["machineOutcome"]["establishedBy"].startswith("deterministic")
    assert view["humanAssessments"][0]["establishedBy"].startswith("a person's judgement")
    assert "verdict" not in view["machineOutcome"], "a human verdict never sits in the machine half"


# --- tenancy -------------------------------------------------------------------------------------


def test_reviews_and_findings_are_workspace_isolated(db: str) -> None:
    journey = _journey(db)
    _submit(db, journey)
    _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)
    with workspace_connection(db, WS_OTHER) as conn:
        for table in ("review", "review_request", "finding", "finding_transition"):
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == [], table  # noqa: S608


def test_another_tenant_cannot_resolve_this_tenants_finding(db: str) -> None:
    _, finding_id = _finding(db, outcome=Outcome.FAIL, status=FindingStatus.REPRODUCED)
    with workspace_connection(db, WS_OTHER) as conn:
        with pytest.raises(reviews.ReviewError, match="no such finding"):
            reviews.transition_finding(
                conn,
                workspace_id=WS_OTHER,
                finding_id=finding_id,
                to_status=FindingStatus.DISMISSED,
                actor_id=REVIEWER,
                actor_role=Role.REVIEWER,
                reason="not mine to dismiss",
                expected_revision=1,
            )
