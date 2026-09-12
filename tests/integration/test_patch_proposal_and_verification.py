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
def project(db: str) -> str:
    with workspace_connection(db, WS) as conn:
        return project_store.create_project(conn, workspace_id=WS, name="patched")


@pytest.fixture()
def manifest(db: str, project: str, seal_manifest: Callable[..., str]) -> str:
    return seal_manifest(db, workspace_id=WS, project_id=project, authorized_by=OWNER)


@pytest.fixture()
def base_source(db: str, manifest: str) -> str:
    """The source tree this manifest actually sealed, which is now checked."""
    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            """
            SELECT s.tree_digest FROM sealed_manifest m
              JOIN source_snapshot s ON s.id = m.source_snapshot_id
             WHERE m.manifest_digest = %s LIMIT 1
            """,
            (manifest,),
        ).fetchone()
    assert row is not None
    return str(row["tree_digest"])


@pytest.fixture()
def surface(db: str, project: str) -> tuple[str, ...]:
    """A configured repair surface, without which no proposal is accepted at all."""
    with workspace_connection(db, WS) as conn:
        return patches.configure_repair_surface(
            conn,
            workspace_id=WS,
            project_id=project,
            # The lockfile sits at the repository root, so a project that wants dependency repairs
            # has to say so explicitly -- which is the point of the surface being configuration.
            paths=("src", "pnpm-lock.yaml"),
            configured_by=OWNER,
        )


@pytest.fixture()
def finding(db: str, manifest: str, project: str, surface: tuple[str, ...]) -> tuple[str, str]:
    """A reproduced finding on a failed run of this project's sealed manifest."""
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(
            conn, workspace_id=WS, manifest_digest=manifest, project_id=project
        )
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
    if source_digest is None:
        with workspace_connection(db, WS) as conn:
            row = conn.execute(
                "SELECT s.tree_digest FROM sealed_manifest m "
                "  JOIN source_snapshot s ON s.id = m.source_snapshot_id "
                " WHERE m.manifest_digest = %s LIMIT 1",
                (manifest,),
            ).fetchone()
        source_digest = str(row["tree_digest"]) if row else str(digest({"source": "unsealed"}))
    with workspace_connection(db, WS) as conn:
        return patches.propose_patch(
            conn,
            workspace_id=WS,
            finding_id=finding_id,
            base_manifest_digest=manifest,
            base_source_digest=source_digest,
            changes=changes,
            rationale="associate the label with the input so the error is announced",
            proposed_by=OWNER,
            **kwargs,  # type: ignore[arg-type]
        )


def _approve(
    db: str, patch: patches.PatchProposal, *, seconds: int = 3600
) -> tuple[patches.PatchProposal, str]:
    """Approve through the real path, which mints the approval after the transition."""
    with workspace_connection(db, WS) as conn:
        approved = patches.approve_patch(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            actor_id=OWNER,
            expires_at=to_rfc3339_utc(datetime.now(UTC) + timedelta(seconds=seconds)),
            expected_revision=patch.revision,
        )
    assert approved.approval_id is not None
    return approved, approved.approval_id


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
    """An approval binding to inputs nothing recorded authorizes an identity matching nothing.

    The refusal now names the run's own manifest, because the base is checked against the finding's
    run rather than against anything this workspace happened to seal.
    """
    finding_id, _ = finding
    with pytest.raises(patches.PatchError, match="not the manifest the finding's run used"):
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


def _attach_bogus_approval(db: str, patch: patches.PatchProposal, **overrides: object) -> str:
    """Record an approval that does not authorize this patch, and attach it by hand.

    `approve_patch` mints its own approval and cannot be handed a wrong one, which is the point --
    but it means the only way to exercise the dispatch check is to write the row some other code
    path could one day produce. The UPDATE is deliberate and is the whole reason this helper is
    ugly: it simulates a corrupted or maliciously written proposal, which is exactly what
    `assert_dispatchable` exists to refuse.
    """
    fields: dict[str, object] = {
        "scope": ApprovalScope.PATCH_APPLY,
        "target_id": patch.patch_id,
        "target_digest": patch.patch_digest,
        "expected_revision": patch.revision,
        "expires_at": to_rfc3339_utc(datetime.now(UTC) + timedelta(hours=1)),
    }
    fields.update(overrides)
    with workspace_connection(db, WS) as conn:
        approval_id = approvals.record_approval(
            conn,
            workspace_id=WS,
            actor_id=OWNER,
            **fields,  # type: ignore[arg-type]
        )
        conn.execute(
            "UPDATE patch_proposal SET approval_id = %s, status = 'APPROVED' WHERE id = %s",
            (approval_id, patch.patch_id),
        )
    return approval_id


def _dispatch_refusal(db: str, patch: patches.PatchProposal, match: str) -> None:
    with workspace_connection(db, WS) as conn:
        current = patches.load_patch(conn, patch_id=patch.patch_id)
        with pytest.raises(patches.PatchError, match=match):
            patches.assert_dispatchable(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                current_source_digest=current.base_source_digest,
            )


def test_an_approval_for_a_different_scope_does_not_authorize_dispatch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """RUN_EFFECTS is not a weaker PATCH_APPLY. Scopes do not imply one another."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    _attach_bogus_approval(db, patch, scope=ApprovalScope.RUN_EFFECTS)
    _dispatch_refusal(db, patch, "scope")


def test_an_approval_bound_to_other_bytes_does_not_authorize_dispatch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A changed digest is not a weaker authorization. It is not an authorization."""
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    _attach_bogus_approval(db, patch, target_digest=str(digest({"different": "patch"})))
    _dispatch_refusal(db, patch, "digest has changed")


def test_an_expired_approval_does_not_authorize_dispatch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    _attach_bogus_approval(
        db, patch, expires_at=to_rfc3339_utc(datetime.now(UTC) - timedelta(seconds=1))
    )
    _dispatch_refusal(db, patch, "expired")


def test_an_approval_for_an_older_revision_does_not_authorize_dispatch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The check the previous version could not make.

    `assert_dispatchable` compared the approval's `expected_revision` against itself, which is a
    check that can never fail -- so a patch whose revision had moved since approval stayed
    dispatchable. The approval now binds to the revision the patch has once approved, and anything
    that moves it afterwards voids the approval here.
    """
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    _attach_bogus_approval(db, patch, expected_revision=patch.revision - 1)
    _dispatch_refusal(db, patch, "expects revision")


def test_a_legitimate_approval_remains_dispatchable(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The other half, and the reason the ordering had to be repaired rather than the check removed.

    Approving bumps the patch's revision, so an approval minted before the transition records the
    old number and a correct dispatch check would reject every real approval. Minting after the
    transition is what makes a genuine approval survive a genuine check.
    """
    finding_id, _ = finding
    approved, approval_id = _approve(db, _propose(db, finding_id, manifest))
    with workspace_connection(db, WS) as conn:
        stored = approvals.load_for_check(conn, approval_id=approval_id)
        assert stored.expected_revision == approved.revision
        dispatched = patches.assert_dispatchable(
            conn,
            workspace_id=WS,
            patch_id=approved.patch_id,
            current_source_digest=approved.base_source_digest,
        )
    assert dispatched.status is PatchStatus.APPROVED


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


def _open(db: str, patch: patches.PatchProposal, baseline_run_id: str) -> str:
    with workspace_connection(db, WS) as conn:
        record = patches.open_verification(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            baseline_run_id=baseline_run_id,
            baseline_identity={},
        )
    return record.verification_id


def _completed_run(
    db: str, manifest: str, project: str, outcome: str, status: str = "COMPLETED"
) -> str:
    """A run recorded as completed with an outcome, written the way the product writes one.

    Straight to the `run` row rather than through a reducer: this suite is about the verification
    gates, and the gates read recorded outcomes. What matters is that the outcome is *in the
    database* rather than supplied to the function being tested -- which is exactly what the review
    required.
    """
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(
            conn, workspace_id=WS, manifest_digest=manifest, project_id=project
        )
        # One statement: a terminal run is immutable, so a second update to change the status
        # would be refused by the trigger that protects exactly that.
        conn.execute(
            "UPDATE run SET status = %s, outcome = %s, execution_began = true, "
            "    ambiguity_reason = %s WHERE id = %s",
            (
                status,
                outcome,
                # An interrupted run must say why it is ambiguous; the schema refuses one that does
                # not, which is the same rule a real interruption goes through.
                "STOP_NOT_ACKNOWLEDGED" if status == "INTERRUPTED" else None,
                run_id,
            ),
        )
    return run_id


def _conclude(db: str, verification_id: str, **kwargs: object) -> patches.VerificationRecord:
    with workspace_connection(db, WS) as conn:
        return patches.conclude_verification(
            conn,
            workspace_id=WS,
            verification_id=verification_id,
            **kwargs,  # type: ignore[arg-type]
        )


def test_conclude_verification_accepts_no_evidence_from_its_caller(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The design claim, tightened by review.

    It was already true that no caller could pass a *conclusion*. But the previous version took a
    `CandidateEvidence` the caller filled in -- the baseline outcome, the candidate outcome, the
    watermarks, the regression result -- and derived VERIFIED from it. Anything that could call it
    could therefore assert a repair had been verified without a run having happened.

    So the signature carries identifiers and nothing else. Asserted against the signature, because a
    future parameter that reintroduces caller-supplied evidence should fail here rather than in a
    review.
    """
    import inspect

    parameters = set(inspect.signature(patches.conclude_verification).parameters)
    assert parameters == {
        "conn",
        "workspace_id",
        "verification_id",
        "candidate_run_id",
        "permitted_differences",
        "now",
    }
    # And no type exists for a caller to fill in.
    assert not hasattr(patches, "CandidateEvidence")


def test_verified_is_unreachable_while_no_runner_attests_regressions(
    db: str, finding: tuple[str, str], manifest: str, project: str
) -> None:
    """A complete-looking pair still cannot reach VERIFIED here, and says why.

    Baseline FAIL, candidate PASS, candidate COMPLETED, identical sealed identity, producers closed:
    every gate the database can speak to is satisfied. It is still INCONCLUSIVE, because nothing in
    this deployment executes the application's protected functional tests, so whether the repair
    broke validation or authorization is unknown -- and an unknown gate is an unmet gate.

    This is the honest state of FR-011 here, and it is asserted rather than described so that a
    change which makes VERIFIED reachable without a runner fails this test.
    """
    finding_id, baseline = finding
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE run SET status = 'COMPLETED', outcome = 'FAIL', execution_began = true "
            " WHERE id = %s",
            (baseline,),
        )
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)
    candidate = _completed_run(db, manifest, project, "PASS")
    with workspace_connection(db, WS) as conn:
        attempt = runs.start_attempt(conn, run_id=candidate, workspace_id=WS, lease_epoch=1)
        for producer in ("supervisor:mac-01", "observer-1"):
            conn.execute(
                "INSERT INTO producer_stream (workspace_id, run_id, attempt_id, producer_id, "
                "    admitted_through, closed_at_sequence, closed_at) "
                "VALUES (%s, %s, %s, %s, 1, 1, now())",
                (WS, candidate, attempt, producer),
            )

    record = _conclude(db, verification_id, candidate_run_id=candidate)

    assert record.conclusion == "INCONCLUSIVE"
    assert any("protected functional regressions" in r for r in record.reasons)
    assert any("unknown gate is an unmet gate" in r for r in record.reasons)
    # Every other gate is quiet, which is what makes this the regression-only case.
    assert not any("not FAIL" in r or "not PASS" in r for r in record.reasons)
    with workspace_connection(db, WS) as conn:
        assert patches.load_patch(conn, patch_id=approved.patch_id).status is PatchStatus.FAILED


def test_a_caller_cannot_assert_a_baseline_failure_that_the_run_does_not_record(
    db: str, finding: tuple[str, str], manifest: str, project: str
) -> None:
    """The baseline outcome is read from the run, not taken from the caller.

    A baseline that never failed means a passing candidate proves the behaviour was never broken
    (INV-02). Previously the caller simply said `baseline_outcome="FAIL"`.
    """
    finding_id, baseline = finding
    # The baseline run is left as created: NOT_EVALUATED, not FAIL.
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)
    candidate = _completed_run(db, manifest, project, "PASS")

    record = _conclude(db, verification_id, candidate_run_id=candidate)

    assert record.conclusion == "INCONCLUSIVE"
    assert any("not FAIL" in r for r in record.reasons)


def test_an_open_verification_claims_nothing(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    with workspace_connection(db, WS) as conn:
        record = patches.load_verification(conn, verification_id=verification_id)
        patch = patches.load_patch(conn, patch_id=approved.patch_id)
    assert record.state == "BUILDING"
    assert record.conclusion is None
    assert "Nothing here says the repair works" in record.meaning
    assert patch.status is PatchStatus.BUILDING


def test_a_verification_cannot_open_on_an_unapproved_patch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Building a candidate from an unapproved patch is work nobody authorized."""
    finding_id, baseline = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.VerificationError, match="begins from APPROVED"):
            patches.open_verification(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                baseline_run_id=baseline,
                baseline_identity={},
            )


def test_a_candidate_that_never_ran_is_inconclusive_not_a_rejection(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A run that produced no verdict has not shown the repair failed. It has shown nothing."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    record = _conclude(db, verification_id)

    assert record.conclusion == "INCONCLUSIVE"
    assert any("no candidate run" in r for r in record.reasons)
    assert "nothing about the repair is claimed" in record.meaning


def test_an_interrupted_candidate_carries_no_verdict_a_comparison_can_use(
    db: str, finding: tuple[str, str], manifest: str, project: str
) -> None:
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)
    candidate = _completed_run(db, manifest, project, "INCONCLUSIVE", status="INTERRUPTED")

    record = _conclude(db, verification_id, candidate_run_id=candidate)

    assert record.conclusion == "INCONCLUSIVE"
    assert any("not COMPLETED" in r for r in record.reasons)


def test_a_producer_that_left_its_stream_open_disqualifies_the_candidate(
    db: str, finding: tuple[str, str], manifest: str, project: str
) -> None:
    """INV-06. A contiguous chain does not cover a producer that stopped halfway."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)
    candidate = _completed_run(db, manifest, project, "PASS")
    with workspace_connection(db, WS) as conn:
        attempt = runs.start_attempt(conn, run_id=candidate, workspace_id=WS, lease_epoch=1)
        conn.execute(
            "INSERT INTO producer_stream (workspace_id, run_id, attempt_id, producer_id, "
            "    admitted_through) VALUES (%s, %s, %s, 'observer-1', 1)",
            (WS, candidate, attempt),
        )

    record = _conclude(db, verification_id, candidate_run_id=candidate)

    assert record.conclusion == "INCONCLUSIVE"
    assert any("left their streams open" in r for r in record.reasons)


def test_a_concluded_verification_is_not_rewritten(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A frozen outcome a second call can overwrite is not frozen."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    _conclude(db, verification_id)
    with pytest.raises(patches.VerificationError, match="already concluded"):
        _conclude(db, verification_id)


def test_a_failed_verification_leaves_the_finding_exactly_as_it_was(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A repair that was not established removes nothing."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    record = _conclude(db, verification_id)
    assert record.conclusion == "INCONCLUSIVE"

    with workspace_connection(db, WS) as conn:
        row = conn.execute("SELECT status FROM finding WHERE id = %s", (finding_id,)).fetchone()
        patch = patches.load_patch(conn, patch_id=approved.patch_id)
    assert row is not None and row["status"] == "REPRODUCED"
    assert patch.status is PatchStatus.FAILED


def test_every_attempt_is_listed_rather_than_the_favourable_one(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Reporting all attempts is what separates a verification from a search for a good result."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    first = _open(db, approved, baseline)
    _conclude(db, first)

    with workspace_connection(db, WS) as conn:
        listed = patches.verifications_for_patch(conn, patch_id=approved.patch_id)
    assert [r.verification_id for r in listed] == [first]
    assert listed[0].conclusion == "INCONCLUSIVE"


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


def _body(manifest: str, base_source: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "baseManifestDigest": manifest,
        "baseSourceDigest": base_source,
        "changes": [{"path": "src/components/EmailField.tsx", "content": "<label for='email'>"}],
        "rationale": "associate the label with the input so the error is announced",
    }
    payload.update(overrides)
    return payload


def test_the_route_records_a_proposal_that_claims_nothing(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """A 201 means recorded and policy-clean. It does not mean anybody agreed to apply it."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROPOSED"
    assert body["approvalId"] is None
    assert body["changedPaths"] == ["src/components/EmailField.tsx"]
    assert "establishes nothing about whether it repairs" in body["meaning"]


def test_the_route_refuses_a_protected_path_and_names_it(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """A refusal a caller cannot act on becomes a caller trying variations until one passes."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(
            manifest,
            base_source,
            changes=[{"path": "tests/test_email.py", "content": "assert True"}],
        ),
        headers={CSRF_HEADER: csrf},
    )
    # 400 INVALID_INPUT, this product's code for a well-formed request it will not carry out. Not
    # 403: the caller's authority was fine, the change is not one this product will carry.
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "tests/test_email.py" in detail
    assert "protected tests" in detail


def test_a_reviewer_cannot_approve_a_patch(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Assessing evidence is not authorizing the execution of a patch author's code."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    owner_csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source),
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
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """An approval binds to bytes. If the patch moved, the approver never saw what they approved."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source),
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
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """An approval that never expires is a standing permission to run somebody's patch."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source),
        headers={CSRF_HEADER: csrf},
    ).json()
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={"expiresInSeconds": 999_999},
        headers={CSRF_HEADER: csrf, "If-Match": f'"{created["revision"]}"'},
    )
    assert response.status_code == 400, response.text


def test_opening_a_verification_says_runtime_proof_is_unavailable(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
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
        json=_body(manifest, base_source),
        headers={CSRF_HEADER: csrf},
    ).json()
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/approval",
        json={},
        headers={CSRF_HEADER: csrf, "If-Match": f'"{created["revision"]}"'},
    )

    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/verifications",
        json={"baselineRunId": run_id},
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
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Declining a change is assessment, which is a reviewer's job.

    The reason is required because 'rejected' alone leaves whoever proposes the next patch guessing.
    """
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    owner_csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source),
        headers={CSRF_HEADER: owner_csrf},
    ).json()

    reviewer_csrf = _sign_in(db, api, user_id=REVIEWER)
    match = {"If-Match": f'"{created["revision"]}"'}
    blank = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/rejection",
        json={"reason": "   "},
        headers={CSRF_HEADER: reviewer_csrf, **match},
    )
    assert blank.status_code == 400, blank.text

    rejected = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created['patchId']}/rejection",
        json={"reason": "the label is associated but the error is still not announced"},
        headers={CSRF_HEADER: reviewer_csrf, **match},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"


def test_a_patch_in_another_workspace_is_not_found_rather_than_forbidden(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Uniform 404, so the API is not an oracle for which patch ids exist elsewhere."""
    _sign_in(db, api)
    response = api.get(f"/v1/workspaces/{WS}/patches/{uuid.uuid4()}")  # type: ignore[attr-defined]
    assert response.status_code == 404, response.text


# --- the proposal is a durable diff -------------------------------------------------------------


def test_a_reloaded_proposal_carries_the_exact_bytes_modes_and_deletions(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A proposal that stored only filenames was not a patch.

    Nothing could reload what was proposed, a reviewer could not read the change they were
    approving, and the bytes an approval bound to existed only in the request that created it. This
    asserts the round trip element by element, including the two things a naive schema loses: the
    file mode, and the difference between a deletion and a file whose content nobody recorded.
    """
    finding_id, _ = finding
    proposed = (
        ProposedChange(path="src/a.tsx", content="<label for='email'>", mode="100644"),
        ProposedChange(path="src/gone.tsx", content=None),
        ProposedChange(path="src/b.tsx", content="line one\nline two\n"),
    )
    patch = _propose(db, finding_id, manifest, changes=proposed)

    with workspace_connection(db, WS) as conn:
        reloaded = patches.load_patch(conn, patch_id=patch.patch_id)

    assert reloaded.changes == proposed
    # Order is the proposal's own, so a reviewer reads the diff as it was written.
    assert [c.path for c in reloaded.changes] == ["src/a.tsx", "src/gone.tsx", "src/b.tsx"]
    # And the digest still matches after the round trip, which is what the approval binds to.
    assert patches.patch_digest(reloaded.changes) == reloaded.patch_digest


def test_a_proposal_whose_stored_bytes_no_longer_match_its_digest_refuses_to_load(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Whatever was approved is not what is stored, so there is nothing safe to return.

    The digest is recomputed on every load precisely so this is detectable. Returning the proposal
    with a note would leave a caller free to apply bytes nobody authorized.
    """
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE patch_change SET content = %s WHERE patch_id = %s",
            ("<input aria-hidden='true'>", patch.patch_id),
        )
    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.PatchError, match="does not match its recorded digest"):
            patches.load_patch(conn, patch_id=patch.patch_id)


def test_the_separately_reviewed_list_is_derived_from_the_rulings(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A stored copy could disagree with the rulings it summarises, invisibly.

    A reviewer would be shown an empty list for a patch that moved a lockfile.
    """
    finding_id, _ = finding
    patch = _propose(
        db,
        finding_id,
        manifest,
        changes=(
            ProposedChange(path="src/a.tsx", content="x"),
            ProposedChange(path="pnpm-lock.yaml", content="lockfileVersion: 9"),
        ),
        acknowledge_separate_review=True,
    )
    with workspace_connection(db, WS) as conn:
        reloaded = patches.load_patch(conn, patch_id=patch.patch_id)
    assert reloaded.separately_reviewed_paths == ("pnpm-lock.yaml",)
    assert dict(reloaded.verdicts) == {
        "src/a.tsx": "ALLOWED",
        "pnpm-lock.yaml": "SEPARATELY_REVIEWED",
    }


def test_a_base_source_digest_the_manifest_never_sealed_is_refused(
    db: str, finding: tuple[str, str], manifest: str, base_source: str
) -> None:
    """The base was previously any 64-character hex string the caller chose.

    Which made the stale-base check at dispatch compare the current source tree against a number
    somebody typed -- so a patch could be approved against a tree that was never built, and the
    check that exists to catch a moved base would pass for the wrong reason.
    """
    finding_id, _ = finding
    with pytest.raises(patches.PatchError, match="not the source tree this manifest sealed"):
        _propose(db, finding_id, manifest, source_digest=str(digest({"source": "invented"})))

    # The tree the seal actually names is accepted, and it is what gets recorded.
    patch = _propose(db, finding_id, manifest, source_digest=base_source)
    assert patch.base_source_digest == base_source


def test_a_change_larger_than_the_limit_is_refused(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """An approval over bytes nobody read is not an approval.

    A repair diff is small by construction; this is the limit that keeps "reviewable" true rather
    than aspirational.
    """
    finding_id, _ = finding
    with pytest.raises(patches.PatchError, match="exceed"):
        _propose(
            db,
            finding_id,
            manifest,
            changes=(
                ProposedChange(path="src/huge.tsx", content="x" * (patches.MAX_CHANGE_BYTES + 1)),
            ),
        )


def test_the_route_returns_the_change_bytes_a_reviewer_has_to_read(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """A response listing only filenames asks somebody to authorize a change they cannot see."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(
            manifest,
            base_source,
            changes=[
                {"path": "src/a.tsx", "content": "<label for='email'>", "mode": "100644"},
                {"path": "src/gone.tsx", "content": None},
            ],
        ),
        headers={CSRF_HEADER: csrf},
    )
    assert created.status_code == 201, created.text
    changes = created.json()["changes"]
    assert changes == [
        {
            "path": "src/a.tsx",
            "operation": "MODIFY",
            "content": "<label for='email'>",
            "mode": "100644",
            "binary": False,
        },
        {
            "path": "src/gone.tsx",
            "operation": "DELETE",
            "content": None,
            "mode": None,
            "binary": False,
        },
    ]

    # And reading it back gives the same bytes, which is the point of persisting them.
    fetched = api.get(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/patches/{created.json()['patchId']}"
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["changes"] == changes


def test_a_patch_stripped_of_every_change_refuses_to_load_or_dispatch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Deleting all the rows must not produce an authorized patch that changes nothing.

    The digest comparison was written `if changes and ...`, so zero rows skipped it entirely. What
    came back was the worst available shape: a proposal with its recorded digest, its APPROVED
    status and a live approval, and no changes at all -- something a caller would read as an
    authorized change and apply as nothing, with the approval still vouching for it.

    Both doors are asserted. `load_patch` is the one that detects it, and `assert_dispatchable` is
    the one an applying caller actually goes through; a fix that only closed the first would leave
    the path that matters open if dispatch ever stopped loading the patch.
    """
    finding_id, _ = finding
    approved, approval_id = _approve(db, _propose(db, finding_id, manifest))

    with workspace_connection(db, WS) as conn:
        deleted = conn.execute(
            "DELETE FROM patch_change WHERE patch_id = %s", (approved.patch_id,)
        ).rowcount
        assert deleted == 1
        # The proposal row is untouched: same digest, same status, same approval. That is precisely
        # why the emptiness has to be caught here rather than inferred from anything else.
        row = conn.execute(
            "SELECT patch_digest, status, approval_id FROM patch_proposal WHERE id = %s",
            (approved.patch_id,),
        ).fetchone()
    assert row is not None
    assert str(row["patch_digest"]) == approved.patch_digest
    assert str(row["status"]) == "APPROVED"
    assert str(row["approval_id"]) == approval_id

    with workspace_connection(db, WS) as conn:
        # And the approval is still perfectly valid in its own right, which is the point: nothing
        # about the authorization is wrong, so only the patch can notice it has been emptied.
        approval = approvals.load_for_check(conn, approval_id=approval_id)
        assert approval.revoked is False
        assert approval.target_digest == approved.patch_digest

        with pytest.raises(patches.PatchError, match="no changes at all"):
            patches.load_patch(conn, patch_id=approved.patch_id)

        with pytest.raises(patches.PatchError, match="no changes at all"):
            patches.assert_dispatchable(
                conn,
                workspace_id=WS,
                patch_id=approved.patch_id,
                current_source_digest=approved.base_source_digest,
            )


def test_the_route_refuses_to_serve_a_patch_stripped_of_its_changes(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Through the HTTP surface, because that is where a reviewer would have seen it.

    A 200 carrying an empty `changes` list and an `approvalId` is the response that would get a
    change waved through: it looks like a patch somebody already approved.
    """
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source),
        headers={CSRF_HEADER: csrf},
    ).json()

    with workspace_connection(db, WS) as conn:
        conn.execute("DELETE FROM patch_change WHERE patch_id = %s", (created["patchId"],))

    patch_url = f"/v1/workspaces/{WS}/patches/{created['patchId']}"
    response = api.get(patch_url)  # type: ignore[attr-defined]
    # 404 rather than a 200 with an empty diff. The proposal cannot be served at all, and a uniform
    # not-found is what every other unreadable record here answers.
    assert response.status_code == 404, response.text


# --- the seven findings from adversarial review -------------------------------------------------


def test_an_approval_and_a_rejection_cannot_both_succeed_on_one_patch(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Finding 1. Two reviewers, one patch, two decisions -- exactly one may be recorded.

    The revision check used to be a read followed by a write. Both callers read revision 1, both
    passed the check, the second UPDATE waited on the row lock and then applied anyway: an approval
    and a rejection both succeeded, the last writer deciding, and both in the history as though both
    had been allowed. The revision is now a predicate in the UPDATE, so the loser is told.

    Two real connections, so the second genuinely contends for the row rather than reusing a
    transaction that already holds it.
    """
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)

    with workspace_connection(db, WS) as first:
        rejected = patches.transition_patch(
            first,
            workspace_id=WS,
            patch_id=patch.patch_id,
            to_status=PatchStatus.REJECTED,
            actor_id=REVIEWER,
            reason="not the right fix",
            expected_revision=patch.revision,
        )
    assert rejected.status is PatchStatus.REJECTED

    with workspace_connection(db, WS) as second:
        with pytest.raises(patches.PatchError):
            patches.approve_patch(
                second,
                workspace_id=WS,
                patch_id=patch.patch_id,
                actor_id=OWNER,
                expires_at=to_rfc3339_utc(datetime.now(UTC) + timedelta(hours=1)),
                expected_revision=patch.revision,
            )

    with workspace_connection(db, WS) as conn:
        final = patches.load_patch(conn, patch_id=patch.patch_id)
        history = conn.execute(
            "SELECT to_status FROM patch_proposal_transition WHERE patch_id = %s ORDER BY id",
            (patch.patch_id,),
        ).fetchall()
    assert final.status is PatchStatus.REJECTED
    assert final.approval_id is None
    # One decision in the history, not two. A rejected patch that also shows an approval would be
    # unreadable afterwards: nobody could say which decision stood.
    assert [str(r["to_status"]) for r in history] == ["PROPOSED", "REJECTED"]


def test_the_revision_is_checked_by_the_update_not_by_a_read_before_it(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Finding 1, at the level the race happens.

    A stale revision must be refused by the statement that writes, so that a concurrent move between
    the read and the write cannot be overwritten. Asserted by moving the patch underneath a caller
    that is holding an older revision.
    """
    finding_id, _ = finding
    patch = _propose(db, finding_id, manifest)
    stale_revision = patch.revision

    with workspace_connection(db, WS) as conn:
        patches.transition_patch(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            to_status=PatchStatus.REJECTED,
            actor_id=REVIEWER,
            reason="decided first",
            expected_revision=stale_revision,
        )
    with workspace_connection(db, WS) as conn:
        with pytest.raises(patches.PatchError, match="revision"):
            patches.transition_patch(
                conn,
                workspace_id=WS,
                patch_id=patch.patch_id,
                to_status=PatchStatus.APPROVED,
                actor_id=OWNER,
                reason="decided second",
                expected_revision=stale_revision,
            )


def test_a_patch_cannot_be_proposed_against_another_runs_manifest(
    db: str,
    finding: tuple[str, str],
    manifest: str,
    project: str,
    seal_manifest: Callable[..., str],
) -> None:
    """Finding 2. The base must be the identity the finding's own run used.

    Checking only that the workspace had sealed it meant a patch for a finding about run A could be
    recorded against run B's manifest -- and then every later check agreed with itself: the approval
    bound to B's digest, the stale-base check compared B's tree, and the verification compared a
    candidate against a baseline it was never about.
    """
    finding_id, _ = finding
    other_project = None
    with workspace_connection(db, WS) as conn:
        other_project = project_store.create_project(conn, workspace_id=WS, name="elsewhere")
    other_manifest = seal_manifest(
        db, workspace_id=WS, project_id=other_project, authorized_by=OWNER
    )
    assert other_manifest != manifest

    with pytest.raises(patches.PatchError, match="not the manifest the finding's run used"):
        _propose(db, finding_id, other_manifest)


def test_the_same_idempotency_key_on_another_finding_is_not_a_replay(
    db: str,
    finding: tuple[str, str],
    manifest: str,
    base_source: str,
    project: str,
    api: object,
) -> None:
    """Finding 3. The route key is half of the idempotency identity.

    Sharing one key across every finding meant the same Idempotency-Key on a *different* finding
    replayed the first finding's proposal -- a caller asking to repair one defect and being handed
    the patch for another, with a 201 and somebody else's patch id.
    """
    from accessforge_api.auth import CSRF_HEADER

    first_finding, run_id = finding
    with workspace_connection(db, WS) as conn:
        second_finding = reviews.create_finding(
            conn,
            workspace_id=WS,
            run_id=run_id,
            assertion_id="focus-is-visible",
            summary="the focus ring is not visible on the submit control",
            status=FindingStatus.REPRODUCED,
            run_outcome=Outcome.FAIL,
            actor_id=OWNER,
        )

    csrf = _sign_in(db, api)
    key = str(uuid.uuid4())
    headers = {CSRF_HEADER: csrf, "Idempotency-Key": key}

    first = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{first_finding}/patches",
        json=_body(manifest, base_source),
        headers=headers,
    )
    second = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{second_finding}/patches",
        json=_body(manifest, base_source),
        headers=headers,
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["patchId"] != first.json()["patchId"]
    assert second.json()["findingId"] == second_finding
    assert second.headers.get("Idempotent-Replay") is None


def test_a_duplicate_path_is_refused_as_invalid_input_not_a_database_error(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Finding 4. A caller sending one path twice was told the server broke.

    The UNIQUE constraint caught it, as an integrity error the route turned into a 500. It is a
    malformed request and it says so.
    """
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(
            manifest,
            base_source,
            changes=[
                {"path": "src/a.tsx", "content": "first"},
                {"path": "src/a.tsx", "content": "second"},
            ],
        ),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400, response.text
    assert "src/a.tsx" in response.json()["detail"]
    assert "more than once" in response.json()["detail"]

    # And at the persistence layer too, for a caller that is not the route.
    with pytest.raises(patches.PatchError, match="more than once"):
        _propose(
            db,
            finding_id,
            manifest,
            changes=(
                ProposedChange(path="src/a.tsx", content="first"),
                ProposedChange(path="src/a.tsx", content="second"),
            ),
        )


def test_a_project_with_no_configured_surface_accepts_no_proposals(
    db: str, manifest: str, project: str, seal_manifest: Callable[..., str]
) -> None:
    """Finding 6. Unconfigured must not mean unrestricted.

    The surface used to arrive in the proposal body, so the agent proposing a change chose how far
    it could reach -- and an omitted or empty list disabled confinement entirely, so the laziest
    request got the widest surface.
    """
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(
            conn, workspace_id=WS, manifest_digest=manifest, project_id=project
        )
        finding_id = reviews.create_finding(
            conn,
            workspace_id=WS,
            run_id=run_id,
            assertion_id="error-is-announced",
            summary="no surface configured for this project",
            status=FindingStatus.REPRODUCED,
            run_outcome=Outcome.FAIL,
            actor_id=OWNER,
        )
        conn.execute("DELETE FROM project_repair_surface WHERE project_id = %s", (project,))

    with pytest.raises(patches.PatchError, match="no configured repair surface"):
        _propose(db, finding_id, manifest)


def test_the_surface_cannot_be_supplied_or_widened_by_the_proposal(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Finding 6, through HTTP. The field is gone, and sending it is rejected outright."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source, applicationPaths=["/", "billing"]),
        headers={CSRF_HEADER: csrf},
    )
    # Refused as an unexpected field rather than ignored: a caller that believes it widened the
    # surface must not be told the request succeeded.
    assert response.status_code == 400, response.text


def test_a_repair_surface_prefix_that_escapes_the_tree_is_refused(db: str, project: str) -> None:
    """A surface of `..` or `/` would authorize exactly what the policy refuses on sight."""
    for bad in ("..", "../etc", "/", "~/keys"):
        with workspace_connection(db, WS) as conn:
            with pytest.raises(patches.PatchError, match="not a usable repair-surface prefix"):
                patches.configure_repair_surface(
                    conn,
                    workspace_id=WS,
                    project_id=project,
                    paths=(bad,),
                    configured_by=OWNER,
                )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("binary", "true"),
        ("binary", 1),
        ("mode", "not-a-mode"),
        ("mode", 100644),
    ],
)
def test_change_fields_are_validated_rather_than_coerced(
    db: str,
    finding: tuple[str, str],
    manifest: str,
    base_source: str,
    api: object,
    field: str,
    value: object,
) -> None:
    """Finding 7. `bool("false")` is True and `bool(0)` is False.

    So a JSON string or number silently became a claim about the file: `"binary": "no"` would have
    marked the change binary and refused it for the wrong reason, and a truthy mode became the text
    of whatever was sent -- including a value that is not a file mode at all.
    """
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    change: dict[str, object] = {"path": "src/a.tsx", "content": "x", field: value}
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source, changes=[change]),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    # The *validation* message, not merely some 400. Without this the test passed for the wrong
    # reason: a raw "true" stays truthy, so the policy refused it as a binary replacement and the
    # response still carried the word "binary" -- a mutation check that removed the validation
    # entirely went unnoticed.
    expected = {
        "binary": "must be true or false",
        "mode": "must be omitted or one of",
    }[field]
    assert expected in detail, detail


def test_acknowledge_separate_review_must_be_a_boolean(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """Finding 7. A truthy string must not stand in for a caller understanding the scope."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(manifest, base_source, acknowledgeSeparateReview="yes"),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400, response.text
    assert "acknowledgeSeparateReview" in response.json()["detail"]


def test_a_valid_mode_is_accepted_and_kept(
    db: str, finding: tuple[str, str], manifest: str, base_source: str, api: object
) -> None:
    """The other direction: strictness must not refuse the modes a real patch carries."""
    from accessforge_api.auth import CSRF_HEADER

    finding_id, _ = finding
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/findings/{finding_id}/patches",
        json=_body(
            manifest,
            base_source,
            changes=[{"path": "src/a.tsx", "content": "x", "mode": "100755", "binary": False}],
        ),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["changes"][0]["mode"] == "100755"


# --- residual blockers from the final review
# -------------------------------------------------------


def test_a_verification_cannot_open_under_a_revoked_approval(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """A status saying APPROVED is not an approval that still authorizes anything.

    `open_verification` checked only the status, so a revoked approval still produced a verification
    record and moved the patch to BUILDING: a candidate begun under an authorization that no longer
    existed, with a row afterwards implying somebody had granted one. Asserted on both effects --
    nothing inserted and the patch not moved -- because either alone would be the damage.
    """
    finding_id, baseline = finding
    approved, approval_id = _approve(db, _propose(db, finding_id, manifest))

    with workspace_connection(db, WS) as conn:
        assert approvals.revoke_approval(conn, approval_id=approval_id) is True
        with pytest.raises(patches.VerificationError, match="revoked"):
            patches.open_verification(
                conn,
                workspace_id=WS,
                patch_id=approved.patch_id,
                baseline_run_id=baseline,
                baseline_identity={},
            )

    with workspace_connection(db, WS) as conn:
        assert patches.verifications_for_patch(conn, patch_id=approved.patch_id) == []
        assert patches.load_patch(conn, patch_id=approved.patch_id).status is PatchStatus.APPROVED


def test_a_verification_cannot_open_under_an_expired_approval(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """Expiry is the same class of failure as revocation, and was equally unchecked here."""
    finding_id, baseline = finding
    patch = _propose(db, finding_id, manifest)
    with workspace_connection(db, WS) as conn:
        approved = patches.approve_patch(
            conn,
            workspace_id=WS,
            patch_id=patch.patch_id,
            actor_id=OWNER,
            expires_at=to_rfc3339_utc(datetime.now(UTC) + timedelta(seconds=1)),
            expected_revision=patch.revision,
        )
        with pytest.raises(patches.VerificationError, match="expired"):
            patches.open_verification(
                conn,
                workspace_id=WS,
                patch_id=approved.patch_id,
                baseline_run_id=baseline,
                baseline_identity={},
                # An hour later, without waiting for one.
                now=datetime.now(UTC) + timedelta(hours=1),
            )
    with workspace_connection(db, WS) as conn:
        assert patches.verifications_for_patch(conn, patch_id=approved.patch_id) == []


def test_a_verification_cannot_open_when_the_patch_moved_after_approval(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The approval binds to a revision. A patch that moved since is not the one approved."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    with workspace_connection(db, WS) as conn:
        # Any later move bumps the revision. A status change is the honest way to cause one.
        conn.execute(
            "UPDATE patch_proposal SET revision = revision + 1 WHERE id = %s",
            (approved.patch_id,),
        )
        with pytest.raises(patches.VerificationError, match="expects revision"):
            patches.open_verification(
                conn,
                workspace_id=WS,
                patch_id=approved.patch_id,
                baseline_run_id=baseline,
                baseline_identity={},
            )


def test_two_conclusions_racing_do_not_both_succeed(
    db: str, finding: tuple[str, str], manifest: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen outcome that the second finisher overwrites is not frozen.

    The read at the top of `conclude_verification` was not the check: two callers both passed it,
    both computed their own gates, and both wrote. State and revision are now predicates in the
    statement that writes, so the loser is told and the first verdict stands.

    The loser of a real race is a caller that read the record *before* the winner committed and
    reaches its UPDATE afterwards. That is reproduced by making this caller's entry read return the
    pre-conclusion row -- the read is the double, the function under test is untouched, and it
    genuinely arrives at the production UPDATE holding a stale revision.
    """
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    with workspace_connection(db, WS) as conn:
        stale = patches.load_verification(conn, verification_id=verification_id)
    assert stale.state == "BUILDING"

    first = _conclude(db, verification_id)
    assert first.conclusion == "INCONCLUSIVE"

    real_load = patches.load_verification
    calls = {"n": 0}

    def stale_first(*args: object, **kwargs: object) -> patches.VerificationRecord:
        calls["n"] += 1
        if calls["n"] == 1:
            # What the losing caller is holding: open, at the revision before the winner wrote.
            return stale
        return real_load(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(patches, "load_verification", stale_first)
    with pytest.raises(patches.VerificationError, match="concluded by somebody else"):
        _conclude(db, verification_id)
    monkeypatch.undo()

    with workspace_connection(db, WS) as conn:
        final = patches.load_verification(conn, verification_id=verification_id)
    # The first verdict stands, unchanged, and was not bumped by the loser's attempt.
    assert final.conclusion == "INCONCLUSIVE"
    assert final.revision == first.revision


def test_concluding_twice_through_the_function_is_refused(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The same guarantee through the real entry point rather than a hand-written UPDATE."""
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    _conclude(db, verification_id)
    with pytest.raises(patches.VerificationError, match="already concluded"):
        _conclude(db, verification_id)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"field": "runner_profile_digest"}, "no `candidate` key"),
        ({"candidate": "anything"}, "non-empty string `field`"),
        ({"field": "", "candidate": "x"}, "non-empty string `field`"),
        ({"field": 7, "candidate": "x"}, "non-empty string `field`"),
        ("runner_profile_digest", "not an object"),
    ],
)
def test_a_malformed_permitted_difference_is_refused(
    db: str,
    finding: tuple[str, str],
    manifest: str,
    entry: object,
    expected: str,
) -> None:
    """These are the only things allowed to excuse a difference, so a malformed one cannot pass.

    `dict.get` collapses "absent" and "null", and `str(None)` is the string "None" -- so an entry
    that forgot to say what the candidate value was could waive a drift whose candidate value
    happened to be absent, which is most of them.
    """
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    with pytest.raises(patches.VerificationError, match=expected):
        _conclude(db, verification_id, permitted_differences=(entry,))

    # And nothing was written: the validation happens before the record is touched.
    with workspace_connection(db, WS) as conn:
        assert patches.load_verification(conn, verification_id=verification_id).state == "BUILDING"


def test_a_permitted_difference_missing_its_candidate_cannot_waive_real_drift(
    db: str,
    finding: tuple[str, str],
    manifest: str,
    project: str,
    seal_manifest: Callable[..., str],
) -> None:
    """The specific waiver that used to work.

    `{"field": "runner_profile_digest"}` with no candidate value stringified to ("…", "None") and
    matched any candidate whose value the comparison also stringified to "None". Now it is refused
    outright, and the drift it was aimed at is still reported.
    """
    finding_id, baseline = finding
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE run SET status = 'COMPLETED', outcome = 'FAIL', execution_began = true "
            " WHERE id = %s",
            (baseline,),
        )
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    # A candidate sealed separately, so its identity genuinely differs from the baseline's.
    other_manifest = seal_manifest(db, workspace_id=WS, project_id=project, authorized_by=OWNER)
    candidate = _completed_run(db, other_manifest, project, "PASS")

    with pytest.raises(patches.VerificationError, match="no `candidate` key"):
        _conclude(
            db,
            verification_id,
            candidate_run_id=candidate,
            permitted_differences=({"field": "fixture_digest"},),
        )

    # Concluded without the malformed waiver, the drift is named.
    record = _conclude(db, verification_id, candidate_run_id=candidate)
    assert record.conclusion == "INCONCLUSIVE"
    assert any("unexplained identity drift" in r for r in record.reasons)


def test_an_explicit_null_candidate_is_accepted_and_waives_only_a_null(
    db: str, finding: tuple[str, str], manifest: str
) -> None:
    """The sentinel distinguishes absent from null, so stating null is allowed.

    Otherwise the strictness would refuse a legitimate waiver for a field the candidate genuinely
    does not have.
    """
    finding_id, baseline = finding
    approved, _ = _approve(db, _propose(db, finding_id, manifest))
    verification_id = _open(db, approved, baseline)

    record = _conclude(
        db, verification_id, permitted_differences=({"field": "fixture_digest", "candidate": None},)
    )
    assert record.conclusion == "INCONCLUSIVE"
    # Accepted: the refusals are about the missing key, not about the value being null.
    assert not any("candidate` key" in r for r in record.reasons)
