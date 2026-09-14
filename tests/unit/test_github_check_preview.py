"""Synthetic trusted facts test rendering only; no original evaluation or publication proof."""

from dataclasses import replace
from uuid import UUID

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.verdict import PASS_SCOPE_STATEMENT
from accessforge_domain.states import Outcome, RunStatus
from accessforge_orchestrator.github_access import RepositoryScope
from accessforge_orchestrator.github_check_preview import CheckFacts, Refused, preview_check

SCOPE = RepositoryScope(7, 42, 3, 13, "fixture-owner", "fixture-repository")
FACTS = CheckFacts(
    str(UUID(int=1)),
    str(UUID(int=2)),
    str(UUID(int=3)),
    "a" * 40,
    "b" * 64,
    "c" * 64,
    "d" * 64,
    RunStatus.QUEUED,
    Outcome.NOT_EVALUATED,
    False,
)


@pytest.mark.parametrize(
    "state,outcome,began,conclusion",
    [
        (RunStatus.QUEUED, Outcome.NOT_EVALUATED, False, None),
        (RunStatus.LEASED, Outcome.NOT_EVALUATED, False, None),
        (RunStatus.RUNNING, Outcome.NOT_EVALUATED, True, None),
        (RunStatus.FINALIZING, Outcome.NOT_EVALUATED, True, None),
        (RunStatus.COMPLETED, Outcome.PASS, True, "success"),
        (RunStatus.COMPLETED, Outcome.FAIL, True, "failure"),
        (RunStatus.COMPLETED, Outcome.INCONCLUSIVE, True, "action_required"),
        (RunStatus.INTERRUPTED, Outcome.INCONCLUSIVE, True, "action_required"),
        (RunStatus.CANCELLED, Outcome.NOT_EVALUATED, False, "cancelled"),
        (RunStatus.CANCELLED, Outcome.INCONCLUSIVE, True, "cancelled"),
    ],
)
def test_honest_outcome_mapping(
    state: RunStatus, outcome: Outcome, began: bool, conclusion: str | None
) -> None:
    facts = replace(
        FACTS,
        status=state,
        outcome=outcome,
        execution_began=began,
        evaluation_digest="e" * 64 if state is RunStatus.COMPLETED else None,
    )
    result = preview_check(SCOPE, facts)
    body = result["request"]["body"]
    assert body.get("conclusion") == conclusion
    assert body["head_sha"] == "a" * 40
    assert PASS_SCOPE_STATEMENT in body["output"]["summary"]
    assert "Human review remains separate" in body["output"]["summary"]
    assert result["requiredApproval"] == "GITHUB_PUBLISH"
    assert result["previewDigest"] == digest(
        {k: v for k, v in result.items() if k != "previewDigest"}
    )
    if conclusion is None:
        assert "conclusion" not in body
        assert body["status"] in {"queued", "in_progress"}
    else:
        assert body["status"] == "completed"


def test_identity_and_payload_changes_invalidate_preview_digest() -> None:
    original = preview_check(SCOPE, FACTS)
    for facts in (
        replace(FACTS, source_sha="f" * 40),
        replace(FACTS, journey_digest="f" * 64),
        replace(FACTS, profile_digest="f" * 64),
        replace(FACTS, manifest_digest="f" * 64),
        replace(FACTS, binding_id=str(UUID(int=4))),
        replace(FACTS, status=RunStatus.RUNNING, execution_began=True),
    ):
        assert preview_check(SCOPE, facts)["previewDigest"] != original["previewDigest"]
    assert (
        preview_check(replace(SCOPE, repository_id=14), FACTS)["previewDigest"]
        != original["previewDigest"]
    )
    # An input is not an output dictionary alias, and old output mutation cannot affect a rerender.
    original["request"]["body"]["conclusion"] = "success"
    assert "conclusion" not in preview_check(SCOPE, FACTS)["request"]["body"]


def test_external_discovery_identity_is_stable_but_not_an_approval() -> None:
    before = preview_check(SCOPE, FACTS)
    after = preview_check(SCOPE, replace(FACTS, status=RunStatus.RUNNING, execution_began=True))
    assert before["request"]["body"]["external_id"] == after["request"]["body"]["external_id"]
    assert before["previewDigest"] != after["previewDigest"]
    assert after["meaning"] == "LOCAL_PREVIEW_NOT_PUBLICATION_AUTHORITY"


def test_missing_original_evaluation_or_impossible_status_refuses() -> None:
    with pytest.raises(Refused):
        replace(FACTS, status=RunStatus.COMPLETED, outcome=Outcome.PASS, execution_began=True)
    with pytest.raises(Refused):
        replace(FACTS, outcome=Outcome.PASS)
    with pytest.raises(Refused):
        replace(FACTS, evaluation_digest="e" * 64)
    with pytest.raises(Refused):
        replace(FACTS, source_sha="main")
