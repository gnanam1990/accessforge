"""Proposing a repair, authorizing it exactly, and refusing to call it verified.

FR-010 and FR-011. The tests are organised around the three claims that must not blur: a proposal is
a request, an approval is permission to try, and VERIFIED is a statement about evidence.

The test that justifies the design is `no caller can write a conclusion`. `conclude_verification`
takes no verdict argument, so there is no route, retry or reviewer that can mark an inconclusive
candidate VERIFIED -- which CONTRACTS forbids in words and this makes unreachable in code.

**Runtime proof is BLOCKED and the tests say so rather than faking it.** A real verification needs a
candidate built inside a containment boundary this deployment has not got, and run against a real
screen reader on a real desktop. So the VERIFIED path here is exercised by handing the gates
fabricated evidence, and it is named `_fabricated_` wherever that happens: it proves the gates
accept a complete pair, not that this product has ever produced one.

Requirements: FR-002, FR-007, FR-010, FR-011. Invariants: INV-02, INV-03, INV-04, INV-06, INV-16.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.outcome import Outcome
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import ApprovalScope, FindingStatus, PatchStatus
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import (
    approvals,
    assert_row_level_security_enforced,
    migrate,
    patches,
    reviews,
    runs,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence import (
    projects as project_store,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x410))
OWNER = str(uuid.UUID(int=0x411))
REVIEWER = str(uuid.UUID(int=0x412))

FIX = (ProposedChange(path="src/components/EmailField.tsx", content="<label for='email'>"),)


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'patches')", (WS,))
        for user, email in ((OWNER, "owner@example.test"), (REVIEWER, "reviewer@example.test")):
            conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, email))
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OWNER),
        )
    yield test_database_url


@pytest.fixture()
def manifest(db: str, seal_manifest: Callable[..., str]) -> str:
    with workspace_connection(db, WS) as conn:
        project_id = project_store.create_project(conn, workspace_id=WS, name="patched")
    return seal_manifest(db, workspace_id=WS, project_id=project_id, authorized_by=OWNER)


@pytest.fixture()
def finding(db: str, manifest: str) -> tuple[str, str]:
    """A reproduced finding on a failed run: the only thing a repair can be proposed against."""
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=manifest)
        finding_id = reviews.create_finding(
            conn,
            workspace_id=WS,
            run_id=run_id,
            assertion_id="error-is-announced",
            summary="the invalid-email error is not announced to the reader",
            status=FindingStatus.REPRODUCED,
            run_outcome=Outcome.FAIL,
            actor_id=OWNER,
        )
    return finding_id, run_id


def _propose(
    db: str,
    finding_id: str,
    manifest: str,
    *,
    changes: tuple[ProposedChange, ...] = FIX,
    source_digest: str | None = None,
    **kwargs: object,
) -> patches.PatchProposal:
    with workspace_connection(db, WS) as conn:
        return patches.propose_patch(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            base_manifest_digest=manifest,
            base_source_digest=source_digest or str(digest({"source": "base"})),
            changes=changes,
            rationale="associate the label with the input so the error is announced",
            proposed_by=OWNER,
            **kwargs,  # type: ignore[arg-type]
        )


def _approve(
    db: str, patch: patches.PatchProposal, *, seconds: int = 3600
) -> tuple[patches.PatchProposal, str]:
    with workspace_connection(db, WS) as conn:
        approval_id = approvals.record_approval(
            conn,
            workspace_id=WS,
            scope=ApprovalScope.PATCH_APPLY,
            actor_id=OWNER,
            target_id=patch.patch_id,
            target_digest=patch.patch_digest,
            expected_revision=patch.revision,
            expires_at=to_rfc3339_utc(datetime.now(UTC) + timedelta(seconds=seconds)),
        )
        approved = patches.approve_patch(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            approval_id=approval_id,
            actor_id=OWNER,
        )
    return approved, approval_id


# --- proposing ----------------------------------------------------------------------------------


def test_a_proposal_records_the_base_identity_in_both_halves(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Manifest *and* source digest.

    Recording only the manifest would let the source tree move underneath a live approval, which is
    the stale-base failure: a diff applied to a tree it was not written against is a different
    change.
    """
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)

    assert patch.status is PatchStatus.PROPOSED
    assert patch.base_manifest_digest == manifest
    assert len(patch.base_source_digest) == 64
    assert patch.changed_paths == ("src/components/EmailField.tsx",)
    assert "establishes nothing about whether it repairs" in patch.meaning


def test_a_proposal_against_an_unsealed_base_is_refused(db: str, finding: tuple[str, str]) -> None:
    """An approval binding to inputs nothing recorded authorizes an identity matching nothing."""
    finding_id, _ = finding
    with pytest.raises(patches.PatchError, match="no sealed manifest"):
        _propose(db, finding_id, str(digest({"never": "sealed"})))


def test_a_patch_touching_a_protected_path_is_refused_not_recorded(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """There is no version of "we considered editing the test" worth storing.

    A REJECTED row would put it in the finding's history as a repair somebody offered, when what
    happened is that the product refused to carry it.
    """
    finding_id, _ = finding
    with pytest.raises(patches.PatchRefused, match="protected tests"):
        _propose(
            db,
            finding_id,
            manifest,
            changes=(ProposedChange(path="tests/test_email.py", content="assert True"),),
        )
    with workspace_connection(db, WS) as conn:
        assert patches.patches_for_finding(conn, finding_id=finding_id) == []


def test_a_dependency_change_needs_acknowledging_before_it_can_be_proposed(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Allowed, but never as a routine accessibility edit."""
    finding_id, _ = finding
    changes = (
        ProposedChange(path="src/components/EmailField.tsx", content="x"),
        ProposedChange(path="pnpm-lock.yaml", content="lockfileVersion: 9"),
    )
    with pytest.raises(patches.PatchRefused, match="dependency or build description"):
        _propose(db, finding_id, manifest, changes=changes)

    patch = _propose(db, finding_id, manifest, changes=changes, acknowledge_separate_review=True)
    # And it is surfaced, not merely permitted: a reviewer told "fixed the label" must be told the
    # lockfile moved, because that changes what the candidate is built from.
    assert patch.separately_reviewed_paths == ("pnpm-lock.yaml",)


def test_an_empty_patch_is_refused(db: str, finding: tuple[str, str], manifest: str) -> None:
    finding_id, _ = finding
    with pytest.raises(patches.PatchError, match="must change something"):
        _propose(db, finding_id, manifest, changes=())


def test_the_digest_covers_paths_and_content_together(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Over content alone, an approval for one patch would authorize another writing the same bytes
    somewhere else."""
    same_bytes_elsewhere = (
        ProposedChange(path="src/components/Other.tsx", content=FIX[0].content),
    )
    assert patches.patch_digest(FIX) != patches.patch_digest(same_bytes_elsewhere)

    changed_bytes = (ProposedChange(path=FIX[0].path, content="something else"),)
    assert patches.patch_digest(FIX) != patches.patch_digest(changed_bytes)

    # Order must not matter: the same patch described in a different order is the same patch.
    pair = (
        ProposedChange(path="a.tsx", content="1"),
        ProposedChange(path="b.tsx", content="2"),
    )
    assert patches.patch_digest(pair) == patches.patch_digest(tuple(reversed(pair)))


def test_every_proposal_for_a_finding_is_listed_including_the_failures(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A finding that took three attempts is a different story from one that took one."""
    finding_id, _ = finding
    first = _propose(db, finding_id, manifest)
    second = _propose(
        db, finding_id, manifest, changes=(ProposedChange(path="src/b.tsx", content="y"),)
    )
    with workspace_connection(db, WS) as conn:
        patches.transition_patch(
            conn,
            workspace_id=WS,
            patch_id=first.patch_id,
            to_status=PatchStatus.REJECTED,
            actor_id=REVIEWER,
            reason="the label is associated but the error is still not announced",
        )
        listed = patches.patches_for_finding(conn, finding_id=finding_id)

    assert [p.patch_id for p in listed] == [first.patch_id, second.patch_id]
    assert listed[0].status is PatchStatus.REJECTED


# --- approving ----------------------------------------------------------------------------------


def test_an_approval_for_a_different_scope_does_not_authorize_a_patch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """RUN_EFFECTS is not a weaker PATCH_APPLY. Scopes do not imply one another."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        wrong = approvals.record_approval(
            conn,
            workspace_id=WS,
            scope=ApprovalScope.RUN_EFFECTS,
            actor_id=OWNER,
            target_id=patch.patch_id,
            target_digest=patch.patch_digest,
            expected_revision=patch.revision,
            expires_at=to_rfc3339_utc(datetime.now(UTC) + timedelta(hours=1)),
        )
        with pytest.raises(patches.PatchError, match="scope"):
            patches.approve_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                approval_id=wrong,
                actor_id=OWNER,
            )


def test_an_approval_bound_to_other_bytes_does_not_authorize_this_patch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A changed digest is not a weaker authorization. It is not an authorization."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        stale = approvals.record_approval(
            conn,
            workspace_id=WS,
            scope=ApprovalScope.PATCH_APPLY,
            actor_id=OWNER,
            target_id=patch.patch_id,
            target_digest=str(digest({"different": "patch"})),
            expected_revision=patch.revision,
            expires_at=to_rfc3339_utc(datetime.now(UTC) + timedelta(hours=1)),
        )
        with pytest.raises(patches.PatchError, match="digest has changed"):
            patches.approve_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                approval_id=stale,
                actor_id=OWNER,
            )


def test_an_expired_approval_does_not_authorize_anything(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        expired = approvals.record_approval(
            conn,
            workspace_id=WS,
            scope=ApprovalScope.PATCH_APPLY,
            actor_id=OWNER,
            target_id=patch.patch_id,
            target_digest=patch.patch_digest,
            expected_revision=patch.revision,
            expires_at=to_rfc3339_utc(datetime.now(UTC) - timedelta(seconds=1)),
        )
        with pytest.raises(patches.PatchError, match="expired"):
            patches.approve_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                approval_id=expired,
                actor_id=OWNER,
            )


def test_a_revoked_approval_blocks_the_next_application(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Revocation stops the next act. It does not undo what was already done, and says so."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    approved, approval_id = _approve(db, patch)
    assert approved.status is PatchStatus.APPROVED

    with workspace_connection(db, WS) as conn:
        assert approvals.revoke_approval(conn, approval_id=approval_id) is True
        # Idempotent: a second revocation is not an error and did not do it.
        assert approvals.revoke_approval(conn, approval_id=approval_id) is False
        with pytest.raises(patches.PatchError, match="revoked"):
            patches.assert_dispatchable(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                current_source_digest=patch.base_source_digest,
            )


def test_a_moved_source_tree_makes_the_approval_stale_at_dispatch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The gap between approving and applying is where this lives.

    A distinct exception type, because the caller's correct response differs: a revoked approval
    means ask again, and a moved base means propose again against what is there now.
    """
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    _approve(db, patch)

    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.StalePatchBase, match="source tree has changed"):
            patches.assert_dispatchable(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                current_source_digest=str(digest({"source": "rebuilt"})),
            )
        # Unchanged, it dispatches.
        ok = patches.assert_dispatchable(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            current_source_digest=patch.base_source_digest,
        )
    assert ok.status is PatchStatus.APPROVED


def test_an_unapproved_patch_cannot_be_dispatched(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.PatchError, match="only an APPROVED patch"):
            patches.assert_dispatchable(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                current_source_digest=patch.base_source_digest,
            )


def test_two_reviewers_acting_on_the_same_revision_do_not_both_succeed(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The second is told the patch moved, rather than overwriting a decision just made."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        patches.transition_patch(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            to_status=PatchStatus.REJECTED,
            actor_id=REVIEWER,
            reason="not the right fix",
            expected_revision=patch.revision,
        )
        with pytest.raises(patches.PatchError, match="revision"):
            patches.transition_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                to_status=PatchStatus.APPROVED,
                actor_id=OWNER,
                reason="looks fine to me",
                expected_revision=patch.revision,
            )


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (PatchStatus.PROPOSED, PatchStatus.VERIFIED),
        (PatchStatus.PROPOSED, PatchStatus.BUILDING),
        (PatchStatus.PROPOSED, PatchStatus.VERIFYING),
    ],
)
def test_the_lifecycle_refuses_a_shortcut_to_verified(
    db: str, finding: tuple[str, str], manifest: str, start: PatchStatus, target: PatchStatus
) -> None:
    """Nothing reaches VERIFIED except VERIFYING, and nothing reaches VERIFYING except BUILDING."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    assert patch.status is start
    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.PatchError, match="cannot move to"):
            patches.transition_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                to_status=target,
                actor_id=OWNER,
                reason="skipping ahead",
            )


def test_a_rejected_patch_is_terminal(db: str, finding: tuple[str, str], manifest: str) -> None:
    """A failed proposal cannot be nudged forward without going back through approval."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        patches.transition_patch(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            to_status=PatchStatus.REJECTED,
            actor_id=REVIEWER,
            reason="wrong approach",
        )
        with pytest.raises(patches.PatchError, match="terminal"):
            patches.transition_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                to_status=PatchStatus.APPROVED,
                actor_id=OWNER,
                reason="reconsidered",
            )


# --- verification: what it takes to establish a repair --------------------------------------------

BASELINE_IDENTITY = {
    "browser": "chromium-132.0.6834.83",
    "reader": "voiceover-macos-15.3",
    "evaluator": "evaluator-4",
    "fixture": "checkout-v7",
    "locale": "en-US",
}


def _fabricated_complete_pair(candidate_run_id: str) -> patches.CandidateEvidence:
    """Everything a matched pair requires, none of it produced by a real run.

    Named for what it is. This deployment has no containment boundary for building a candidate and
    no real screen reader to run one, so there is no honest way to reach VERIFIED here. Handing the
    gates a fabricated complete pair proves the gates accept one; it proves nothing about this
    product having ever produced one, and the handoff says so.
    """
    return patches.CandidateEvidence(
        candidate_run_id=candidate_run_id,
        candidate_identity=dict(BASELINE_IDENTITY),
        baseline_outcome="FAIL",
        candidate_outcome="PASS",
        closing_watermarks=("supervisor", "observer"),
        protected_regressions_passed=True,
        frozen_assertions_unchanged=True,
    )


def _open(db: str, patch: patches.PatchProposal, baseline_run_id: str) -> str:
    with workspace_connection(db, WS) as conn:
        record = patches.open_verification(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            baseline_run_id=baseline_run_id,
            baseline_identity=dict(BASELINE_IDENTITY),
        )
    return record.verification_id


def _candidate_run(db: str, manifest: str) -> str:
    with workspace_connection(db, WS) as conn:
        return runs.create_run(conn, workspace_id=WS, manifest_digest=manifest)


def _conclude(
    db: str, verification_id: str, evidence: patches.CandidateEvidence
) -> patches.VerificationRecord:
    with workspace_connection(db, WS) as conn:
        return patches.conclude_verification(
            conn, workspace_id=WS, verification_id=verification_id, evidence=evidence
        )


def test_conclude_verification_takes_no_verdict_argument(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The test that justifies the design.

    CONTRACTS says human review cannot convert an INCONCLUSIVE candidate to VERIFIED. A
    `conclusion=` parameter is all it would take for a route, a retry or a sufficiently senior
    reviewer to do exactly that, so there is none -- asserted against the signature, because a
    future change that adds one should fail here rather than in a review.
    """
    import inspect

    parameters = inspect.signature(patches.conclude_verification).parameters
    assert "conclusion" not in parameters
    assert "verdict" not in parameters
    assert "outcome" not in parameters
    assert set(parameters) == {"conn", "workspace_id", "verification_id", "evidence", "now"}


def test_a_verification_cannot_open_on_an_unapproved_patch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Building a candidate from an unapproved patch is work nobody authorized.

    And the record of it would imply there had been some.
    """
    finding_id, run_id = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.VerificationError, match="begins from APPROVED"):
            patches.open_verification(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                baseline_run_id=run_id,
                baseline_identity=dict(BASELINE_IDENTITY),
            )


def test_an_open_verification_claims_nothing(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)

    with workspace_connection(db, WS) as conn:
        record = patches.load_verification(conn, verification_id=verification_id)
        patch = patches.load_patch(conn, patch_id=approved.patch_id)
    assert record.state == "BUILDING"
    assert record.conclusion is None
    assert "Nothing here says the repair works" in record.meaning
    # The patch moved with it: a verification is underway, which is not the same as approved.
    assert patch.status is PatchStatus.BUILDING


def test_a_candidate_that_never_ran_is_inconclusive_not_a_rejection(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A run that produced no verdict has not shown the repair failed. It has shown nothing.

    Recording those as the same thing would let an infrastructure problem read as a rejected repair,
    and the next person would go looking for a better patch instead of a working runner.
    """
    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)

    record = _conclude(db, verification_id, patches.CandidateEvidence(baseline_outcome="FAIL"))

    assert record.conclusion == "INCONCLUSIVE"
    assert any("no candidate run" in r for r in record.reasons)
    assert "nothing about the repair is claimed" in record.meaning


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"baseline_outcome": "INCONCLUSIVE"}, "not FAIL"),
        ({"candidate_outcome": "FAIL"}, "not PASS"),
        ({"closing_watermarks": ("supervisor",)}, "missing closing watermarks"),
        ({"protected_regressions_passed": False}, "protected functional regressions"),
        ({"frozen_assertions_unchanged": False}, "frozen assertions"),
    ],
)
def test_each_gate_alone_prevents_verified(
    db: str,
    finding: tuple[str, str],
    manifest: str,
    mutation: dict[str, object],
    expected: str,
) -> None:
    """One missing gate is enough, and the reason names which.

    Parameterised rather than written once with several failures, because a single test asserting
    "not verified" would pass if only one gate worked.
    """
    from dataclasses import replace

    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)
    candidate = _candidate_run(db, manifest)

    evidence = replace(_fabricated_complete_pair(candidate), **mutation)  # type: ignore[arg-type]
    record = _conclude(db, verification_id, evidence)

    assert record.conclusion != "VERIFIED"
    assert any(expected in reason for reason in record.reasons), record.reasons


def test_unexplained_identity_drift_disqualifies_the_pair(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """INV-04. A candidate on a different reader answered a different question.

    Swapping the reader profile is the most dangerous version: the candidate might genuinely pass on
    NVDA while still failing on the VoiceOver profile the finding was about, and the comparison
    would report a repair that nobody can reproduce.
    """
    from dataclasses import replace

    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)
    candidate = _candidate_run(db, manifest)

    drifted = dict(BASELINE_IDENTITY) | {"reader": "nvda-windows-2024.4"}
    evidence = replace(_fabricated_complete_pair(candidate), candidate_identity=drifted)
    record = _conclude(db, verification_id, evidence)

    assert record.conclusion == "NOT_ESTABLISHED"
    assert any("unexplained identity drift in: reader" in r for r in record.reasons)


def test_a_difference_recorded_as_permitted_does_not_disqualify_it(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """INV-04 allows the approved patch and differences explicitly recorded as legitimate.

    A candidate build genuinely does differ -- a new bundle hash, for instance -- and refusing every
    difference would make verification impossible. Refusing every *unrecorded* one is the rule.
    """
    from dataclasses import replace

    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)
    candidate = _candidate_run(db, manifest)

    rebuilt = dict(BASELINE_IDENTITY) | {"bundleHash": "candidate-bundle"}
    evidence = replace(
        _fabricated_complete_pair(candidate),
        candidate_identity=rebuilt,
        permitted_differences=(
            {
                "field": "bundleHash",
                "candidate": "candidate-bundle",
                "why": "the candidate is a fresh build of the patched tree",
            },
        ),
    )
    record = _conclude(db, verification_id, evidence)

    assert record.conclusion == "VERIFIED", record.reasons


def test_a_complete_fabricated_pair_satisfies_the_gates(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Proves the gates accept a complete pair -- not that this product produced one.

    No candidate has been built or run anywhere in this test. The evidence is constructed, and it is
    the only way to exercise this path in a deployment with no containment boundary and no real
    reader. Runtime proof is BLOCKED; see docs/handoffs/15.md.
    """
    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)
    candidate = _candidate_run(db, manifest)

    record = _conclude(db, verification_id, _fabricated_complete_pair(candidate))

    assert record.conclusion == "VERIFIED"
    assert record.candidate_run_id == candidate
    # The meaning refuses the inference somebody will want to make from the word VERIFIED.
    assert "does not mean the application is accessible" in record.meaning
    assert "not a compliance statement" in record.meaning
    with workspace_connection(db, WS) as conn:
        assert patches.load_patch(conn, patch_id=approved.patch_id).status is PatchStatus.VERIFIED


def test_a_failed_verification_leaves_the_finding_exactly_as_it_was(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A repair that was not established removes nothing.

    The finding and all of its evidence survive. The opposite -- a failed repair attempt quietly
    downgrading the finding it failed to fix -- would make trying a patch a way to make a defect go
    away.
    """
    from dataclasses import replace

    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)
    candidate = _candidate_run(db, manifest)

    record = _conclude(
        db,
        verification_id,
        replace(_fabricated_complete_pair(candidate), candidate_outcome="FAIL"),
    )
    assert record.conclusion == "NOT_ESTABLISHED"
    assert "survive this unchanged" in record.meaning

    with workspace_connection(db, WS) as conn:
        row = conn.execute("SELECT status FROM finding WHERE id = %s", (finding_id,)).fetchone()
        patch = patches.load_patch(conn, patch_id=approved.patch_id)
    assert row is not None and row["status"] == "REPRODUCED"
    assert patch.status is PatchStatus.FAILED


def test_a_concluded_verification_is_not_rewritten(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A frozen outcome that a second call can overwrite is not frozen.

    Run another verification instead -- which is why every attempt is listed rather than the latest
    one replacing the record.
    """
    from dataclasses import replace

    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, run_id)
    candidate = _candidate_run(db, manifest)

    _conclude(
        db, verification_id, replace(_fabricated_complete_pair(candidate), candidate_outcome="FAIL")
    )
    with pytest.raises(patches.VerificationError, match="already concluded"):
        _conclude(db, verification_id, _fabricated_complete_pair(candidate))


def test_every_attempt_is_listed_rather_than_the_favourable_one(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Reporting all attempts is what separates a verification from a search for a good result."""
    from dataclasses import replace

    finding_id, run_id = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))

    first = _open(db, approved, run_id)
    _conclude(
        db,
        first,
        replace(_fabricated_complete_pair(_candidate_run(db, manifest)), candidate_outcome="FAIL"),
    )

    with workspace_connection(db, WS) as conn:
        listed = patches.verifications_for_patch(conn, patch_id=approved.patch_id)
    assert [r.verification_id for r in listed] == [first]
    assert listed[0].conclusion == "NOT_ESTABLISHED"


# --- through the HTTP surface ---------------------------------------------------------------------


@pytest.fixture()
def api(db: str) -> Iterator[object]:
    """The real application, with an owner and a reviewer who is not one."""
    import os

    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    settings = ApiSettings(
        database_url=db,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
    )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'REVIEWER')",
            (WS, REVIEWER),
        )
    with TestClient(create_app(settings)) as client:
        yield client


def _sign_in(db: str, client: object, user_id: str = OWNER) -> str:
    from accessforge_api.auth import SESSION_COOKIE, issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=user_id)
    client.cookies.set(SESSION_COOKIE, issued.session_token)  # type: ignore[attr-defined]
    return issued.csrf_token


def _body(manifest: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "baseManifestDigest": manifest,
        "baseSourceDigest": str(digest({"source": "base"})),
        "changes": [{"path": "src/components/EmailField.tsx", "content": "<label for='email'>"}],
        "rationale": "associate the label with the input so the error is announced",
    }
    payload.update(overrides)
    return payload


def test_the_route_records_a_proposal_that_claims_nothing(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """A 201 means recorded and policy-clean. It does not mean anybody agreed to apply it."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED"
    assert body["approvalId"] is None
    assert body["changedPaths"] == ["src/components/EmailField.tsx"]
    assert "establishes nothing about whether it repairs" in body["meaning"]


def test_the_route_refuses_a_protected_path_and_names_it(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """A refusal a caller cannot act on becomes a caller trying variations until one passes."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, changes=[{"path": "tests/test_email.py", "content": "assert True"}]),
        headers={CSRF_HEADER: csrf},
    )
    # 400 INVALID_INPUT, this product's code for a well-formed request it will not carry out. Not
    # 403: the caller's authority was fine, the change is not one this product will carry.
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "tests/test_email.py" in detail
    assert "protected tests" in detail


def test_a_reviewer_cannot_approve_a_patch(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """Assessing evidence is not authorizing the execution of a patch author's code."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    owner_csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest),
        headers={CSRF_HEADER: owner_csrf},
    ).json()

    reviewer_csrf = _sign_in(db, api, user_id=REVIEWER)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={},
        headers={CSRF_HEADER: reviewer_csrf, "If-Match": f'"{created["revision"]}"'},
    )
    assert response.status_code == 403, response.text


def test_approval_requires_the_revision_the_approver_read(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """An approval binds to bytes. If the patch moved, the approver never saw what they approved."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest),
        headers={CSRF_HEADER: csrf},
    ).json()

    stale = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={},
        headers={CSRF_HEADER: csrf, "If-Match": '"99"'},
    )
    assert stale.status_code == 409, stale.text

    ok = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={},
        headers={CSRF_HEADER: csrf, "If-Match": f'"{created["revision"]}"'},
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["status"] == "APPROVED"
    assert body["approvalId"] is not None
    # The one inference nobody may draw from an approval.
    assert "not authorization to merge, publish or deploy" in body["meaning"]


def test_an_approval_cannot_be_issued_without_an_expiry_bound(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """An approval that never expires is a standing permission to run somebody's patch."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest),
        headers={CSRF_HEADER: csrf},
    ).json()
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={"expiresInSeconds": 999_999},
        headers={CSRF_HEADER: csrf, "If-Match": f'"{created["revision"]}"'},
    )
    assert response.status_code == 400, response.text


def test_opening_a_verification_says_runtime_proof_is_unavailable(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """The response refuses to imply a candidate exists.

    No containment boundary for building one and no real reader to run it. Saying so in the response
    is the difference between "not verified yet" and "this deployment cannot verify anything".
    """
    from accessforge_api.auth import CSRF_HEADER

    finding_id, run_id = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest),
        headers={CSRF_HEADER: csrf},
    ).json()
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={},
        headers={CSRF_HEADER: csrf, "If-Match": f'"{created["revision"]}"'},
    )

    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/verifications",
        json={"baselineRunId": run_id, "baselineIdentity": dict(BASELINE_IDENTITY)},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "BUILDING"
    assert body["conclusion"] is None
    assert "no real screen reader attached" in body["runtimeProof"]


def test_no_route_in_the_contract_accepts_a_verification_conclusion(api: object) -> None:
    """The structural half of "human review cannot convert an inconclusive candidate to verified".

    Read from the live application's own schema rather than from a list in this test: a future route
    that accepted a conclusion would appear here without anybody remembering to update an allowlist.
    """
    schema = api.get("/openapi.json").json()  # type: ignore[attr-defined]
    offenders: list[str] = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            body = operation.get("requestBody")
            if not body:
                continue
            rendered = str(body).lower()
            if "conclusion" in rendered or "verdict" in rendered:
                offenders.append(f"{method.upper()} {path}")
    assert offenders == [], f"these routes accept a conclusion: {offenders}"


def test_a_reviewer_can_reject_a_patch_but_must_say_why(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """Declining a change is assessment, which is a reviewer's job.

    The reason is required because 'rejected' alone leaves whoever proposes the next patch guessing.
    """
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    owner_csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest),
        headers={CSRF_HEADER: owner_csrf},
    ).json()

    reviewer_csrf = _sign_in(db, api, user_id=REVIEWER)
    blank = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/rejection",
        json={"reason": "   "},
        headers={CSRF_HEADER: reviewer_csrf},
    )
    assert blank.status_code == 400, blank.text

    rejected = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/rejection",
        json={"reason": "the label is associated but the error is still not announced"},
        headers={CSRF_HEADER: reviewer_csrf},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"


def test_a_patch_in_another_workspace_is_not_found_rather_than_forbidden(
    db: str, finding: tuple[str, str], manifest: str, api: object
) -> None:
    """Uniform 404, so the API is not an oracle for which patch ids exist elsewhere."""
    _sign_in(db, api)
    response = api.get(f"/v1/workspaces/{WS}/patches/{uuid.uuid4()}")  # type: ignore[attr-defined]
    assert response.status_code == 404, response.text
