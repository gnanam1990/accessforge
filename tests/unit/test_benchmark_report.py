"""Synthetic accounting fixtures, not actual benchmark executions or oracle qualification."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from benchmark_report import read_input, report  # noqa: E402


def plan() -> dict[str, Any]:
    return {
        "corpusVersion": "synthetic-test-v1",
        "identity": {
            "sourceCommit": "1" * 40,
            "buildDigest": "2" * 64,
            "profileDigest": "3" * 64,
            "evaluatorDigest": "4" * 64,
            "lockfileDigest": "5" * 64,
            "evidenceClass": "UNIT",
            "versions": {"python": "3.13", "reader": "not-executed"},
        },
        "cases": [
            {"caseId": "clean", "category": "CLEAN", "repetitions": 2, "oracleDigest": "6" * 64},
            {
                "caseId": "seeded",
                "category": "INJECTED_DEFECT",
                "repetitions": 1,
                "oracleDigest": "7" * 64,
            },
            {
                "caseId": "unsupported",
                "category": "UNSUPPORTED",
                "repetitions": 1,
                "oracleDigest": "8" * 64,
            },
        ],
    }


def completed(case: str, outcome: str, digest: str = "a") -> dict[str, Any]:
    return {
        "caseId": case,
        "repetition": 1,
        "identity": plan()["identity"],
        "status": "COMPLETED",
        "outcome": outcome,
        "bundleDigest": digest * 64,
        "durationMs": 1.0,
    }


def test_denominator_keeps_missing_blocked_and_false_results() -> None:
    result = report(
        plan(),
        {
            "observations": [
                completed("clean", "FAIL"),
                completed("seeded", "PASS", "b"),
                {
                    "caseId": "unsupported",
                    "repetition": 1,
                    "identity": plan()["identity"],
                    "status": "BLOCKED",
                    "reasonCode": "NO_PROFILE",
                },
            ]
        },
    )
    assert result["plannedCases"] == 3
    assert result["plannedRepetitions"] == 4
    assert result["counts"]["NOT_RUN"] == 1
    assert result["counts"]["BLOCKED"] == 1
    assert result["declaredFalseDefects"] == result["declaredFalsePasses"] == 1
    assert result["evidenceVerified"] is False
    assert len(result["rows"]) == 4


def test_empty_results_cannot_disappear_from_coverage() -> None:
    result = report(plan(), {"observations": []})
    assert result["counts"]["NOT_RUN"] == 4
    assert result["counts"]["PASS"] == result["counts"]["COMPLETED"] == 0
    assert result["byCategory"]["CLEAN"]["plannedRepetitions"] == 2
    assert result["byCategory"]["EXTERNAL_DEFECT"]["plannedCases"] == 0


@pytest.mark.parametrize(
    "field",
    [
        "sourceCommit",
        "buildDigest",
        "profileDigest",
        "evaluatorDigest",
        "lockfileDigest",
        "evidenceClass",
        "versions",
    ],
)
def test_mixed_identity_and_synthetic_actual_evidence_are_refused(field: str) -> None:
    row = completed("clean", "PASS")
    row["identity"][field] = {
        "sourceCommit": "9" * 40,
        "evidenceClass": "ACTUAL_AT",
        "versions": {"reader": "changed"},
    }.get(field, "9" * 64)
    with pytest.raises(ValueError, match="mixed"):
        report(plan(), {"observations": [row]})


def test_duplicate_slots_and_reused_bundles_cannot_inflate_repetitions() -> None:
    row = completed("clean", "PASS")
    with pytest.raises(ValueError, match="duplicate observation"):
        report(plan(), {"observations": [row, row]})
    other = copy.deepcopy(row)
    other["repetition"] = 2
    with pytest.raises(ValueError, match="independent repetitions"):
        report(plan(), {"observations": [row, other]})


@pytest.mark.parametrize("change", [{"caseId": "unplanned"}, {"repetition": 3}])
def test_result_cannot_expand_its_own_plan(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="outside"):
        report(plan(), {"observations": [{**completed("clean", "PASS"), **change}]})


def test_timeout_and_inconclusive_remain_separate() -> None:
    timeout = {
        "caseId": "clean",
        "repetition": 1,
        "identity": plan()["identity"],
        "status": "TIMED_OUT",
        "reasonCode": "DEADLINE",
    }
    result = report(plan(), {"observations": [timeout, completed("unsupported", "INCONCLUSIVE")]})
    assert result["counts"]["TIMED_OUT"] == result["counts"]["INCONCLUSIVE"] == 1
    assert result["declaredUnsupportedConclusiveVerdicts"] == 0
    assert (
        report(plan(), {"observations": [completed("unsupported", "PASS")]})[
            "declaredUnsupportedConclusiveVerdicts"
        ]
        == 1
    )


def test_closed_input_and_required_completed_evidence() -> None:
    for row in [
        {**completed("clean", "PASS"), "unexpected": True},
        {**completed("clean", "PASS"), "bundleDigest": None},
        {**completed("clean", "PASS"), "durationMs": float("nan")},
        {**completed("clean", "PASS"), "status": "BLOCKED", "reasonCode": "NO_READER"},
    ]:
        with pytest.raises(ValueError):
            report(plan(), {"observations": [row]})
    duplicate = plan()
    duplicate["cases"].append(duplicate["cases"][0])
    with pytest.raises(ValueError, match="duplicate planned"):
        report(duplicate, {"observations": []})


def test_plan_hash_is_stable_but_changes_with_denominator() -> None:
    first = report(plan(), {"observations": []})["planDigest"]
    changed = plan()
    changed["cases"][0]["repetitions"] = 3
    assert first == report(plan(), {"observations": []})["planDigest"]
    assert first != report(changed, {"observations": []})["planDigest"]


def test_duplicate_json_keys_and_oversized_input_refuse(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text('{"observations":[],"observations":[]}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        read_input(path)
    path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        read_input(path)
