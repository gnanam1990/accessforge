"""Account for a predeclared benchmark corpus without attesting caller-supplied evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")]
Category = Literal["CLEAN", "INJECTED_DEFECT", "EXTERNAL_DEFECT", "UNSUPPORTED", "INFRA_FAILURE"]
Outcome = Literal["PASS", "FAIL", "INCONCLUSIVE"]
MAX_INPUT = 2 * 1024 * 1024


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Identity(ClosedModel):
    sourceCommit: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    buildDigest: Digest
    profileDigest: Digest
    evaluatorDigest: Digest
    lockfileDigest: Digest
    evidenceClass: Literal[
        "UNIT",
        "CONTRACT",
        "LOCAL_INTEGRATION",
        "ACTUAL_AT",
        "ACTUAL_MODEL",
        "HUMAN_REVIEW",
        "OPERATIONS",
    ]
    # Exact declared versions, not an automatic discovery or qualification result.
    versions: Annotated[dict[Identifier, Identifier], Field(min_length=1, max_length=20)]


class Case(ClosedModel):
    caseId: Identifier
    category: Category
    repetitions: Annotated[int, Field(ge=1, le=100)]
    oracleDigest: Digest


class Plan(ClosedModel):
    corpusVersion: Identifier
    identity: Identity
    cases: Annotated[list[Case], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def unique_cases(self) -> Plan:
        if len({case.caseId for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate planned case")
        return self


class Observation(ClosedModel):
    caseId: Identifier
    repetition: Annotated[int, Field(ge=1, le=100)]
    identity: Identity
    status: Literal["COMPLETED", "BLOCKED", "TIMED_OUT"]
    outcome: Outcome | None = None
    bundleDigest: Digest | None = None
    durationMs: Annotated[float, Field(ge=0, le=86400000)] | None = None
    reasonCode: Identifier | None = None

    @model_validator(mode="after")
    def complete_record(self) -> Observation:
        if self.status == "COMPLETED":
            if self.outcome is None or self.bundleDigest is None or self.durationMs is None:
                raise ValueError("completed record requires outcome, bundle digest and duration")
        elif self.outcome is not None or self.reasonCode is None:
            raise ValueError("blocked/timeout requires a reason and cannot carry a verdict")
        return self


class Results(ClosedModel):
    observations: Annotated[list[Observation], Field(max_length=100000)]


def report(plan_data: Any, result_data: Any) -> dict[str, Any]:
    """All counts describe supplied records, never cryptographic or actual-reader acceptance.

    A separately frozen plan is essential: results cannot define their own denominator.
    Caller-supplied corpus labels and bundle digests still require independent verification.
    No rate is extrapolated to an unmeasured population.
    """
    plan, results = Plan.model_validate(plan_data), Results.model_validate(result_data)
    cases = {case.caseId: case for case in plan.cases}
    observed: dict[tuple[str, int], Observation] = {}
    bundles: set[str] = set()
    for observation in results.observations:
        case = cases.get(observation.caseId)
        key = (observation.caseId, observation.repetition)
        if case is None or observation.repetition > case.repetitions:
            raise ValueError("observation outside frozen plan")
        if observation.identity != plan.identity:
            raise ValueError("mixed source/build/profile/evaluator/version/evidence cohort")
        if key in observed:
            raise ValueError("duplicate observation; retries cannot replace a result")
        if observation.bundleDigest is not None:
            if observation.bundleDigest in bundles:
                raise ValueError("one bundle cannot count as independent repetitions")
            bundles.add(observation.bundleDigest)
        observed[key] = observation

    counts: Counter[str] = Counter()
    by_category: dict[str, Counter[str]] = {
        category: Counter()
        for category in (
            "CLEAN",
            "INJECTED_DEFECT",
            "EXTERNAL_DEFECT",
            "UNSUPPORTED",
            "INFRA_FAILURE",
        )
    }
    rows: list[dict[str, Any]] = []
    false_defects = false_passes = unsupported_verdicts = 0
    for case in plan.cases:
        category_counts = by_category.setdefault(case.category, Counter())
        category_counts["plannedCases"] += 1
        category_counts["plannedRepetitions"] += case.repetitions
        for repetition in range(1, case.repetitions + 1):
            row = observed.get((case.caseId, repetition))
            disposition = "NOT_RUN" if row is None else row.status
            counts[disposition] += 1
            category_counts[disposition] += 1
            if row is not None and row.outcome is not None:
                counts[row.outcome] += 1
                category_counts[row.outcome] += 1
                false_defects += int(case.category == "CLEAN" and row.outcome == "FAIL")
                false_passes += int(
                    case.category in {"INJECTED_DEFECT", "EXTERNAL_DEFECT"}
                    and row.outcome == "PASS"
                )
                unsupported_verdicts += int(
                    case.category in {"UNSUPPORTED", "INFRA_FAILURE"}
                    and row.outcome != "INCONCLUSIVE"
                )
            rows.append(
                {
                    "caseId": case.caseId,
                    "category": case.category,
                    "repetition": repetition,
                    "oracleDigest": case.oracleDigest,
                    "status": disposition,
                    "outcome": None if row is None else row.outcome,
                    "bundleDigest": None if row is None else row.bundleDigest,
                    "durationMs": None if row is None else row.durationMs,
                    "reasonCode": "NO_RECORD" if row is None else row.reasonCode,
                }
            )
    keys = ("COMPLETED", "BLOCKED", "TIMED_OUT", "NOT_RUN", "PASS", "FAIL", "INCONCLUSIVE")
    plan_bytes = json.dumps(plan.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    return {
        "schemaVersion": 1,
        "kind": "DECLARED_BENCHMARK_ACCOUNTING_NOT_EVIDENCE_VERIFICATION",
        "evidenceVerified": False,
        "corpusVersion": plan.corpusVersion,
        "planDigest": hashlib.sha256(plan_bytes).hexdigest(),
        "identity": plan.identity.model_dump(),
        "plannedCases": len(cases),
        "plannedRepetitions": len(rows),
        "counts": {key: counts[key] for key in keys},
        "byCategory": {
            category: {key: values[key] for key in (*keys, "plannedCases", "plannedRepetitions")}
            for category, values in sorted(by_category.items())
        },
        "declaredFalseDefects": false_defects,
        "declaredFalsePasses": false_passes,
        "declaredUnsupportedConclusiveVerdicts": unsupported_verdicts,
        "rows": rows,
        "limitations": [
            "Labels, versions and evidence references are caller declarations, not verified proof.",
            "No qualification, human acceptance or accuracy is established.",
            "Freeze and retain the plan before execution; this report cannot prove that ordering.",
        ],
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_input(path: Path) -> Any:
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("benchmark input too large")
    return json.loads(data, object_pairs_hook=_unique_object)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = report(read_input(args.plan), read_input(args.results))
    except (OSError, ValueError):
        # Validation errors can contain operator inputs. Do not echo paths, records or raw errors.
        parser.exit(2, "benchmark inputs refused; check the private plan and results\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
