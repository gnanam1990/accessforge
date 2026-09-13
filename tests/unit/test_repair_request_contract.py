"""Closed repair consent and the actual worker profile must not drift."""

from typing import Any
from uuid import uuid4

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.repair_requests import model_profile, validate
from accessforge_orchestrator.repair.worker import RepairAgentProfile


def test_profile_matches_worker_and_consent_never_defaults_to_true() -> None:
    assert model_profile() == RepairAgentProfile().model_dump(mode="json")
    body: dict[str, Any] = {
        key: str(uuid4()) for key in ("diagnosisId", "projectId", "sourceSnapshotId")
    }
    body.update(
        {
            key: "a" * 64
            for key in (
                "diagnosisDigest",
                "manifestDigest",
                "sourceTreeDigest",
                "evaluationDigest",
                "repairSurfaceDigest",
            )
        }
    )
    body.update(
        modelProfileDigest=digest(model_profile()),
        supersedes=None,
        billableCallAcknowledged=True,
        separateReviewAcknowledged=False,
    )
    validate(body)
    for changes in (
        {"billableCallAcknowledged": False},
        {"billableCallAcknowledged": 1},
        {"separateReviewAcknowledged": 0},
        {"modelProfileDigest": "b" * 64},
        {"verdict": "VERIFIED"},
    ):
        with pytest.raises(ValueError):
            validate({**body, **changes})
