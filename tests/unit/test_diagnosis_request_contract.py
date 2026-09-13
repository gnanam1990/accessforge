"""Pure request boundary checks; no source, database, provider or reader calls."""

from __future__ import annotations

from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.diagnosis_requests import model_profile, validate
from accessforge_orchestrator.diagnosis.agent import DiagnosisAgentProfile


def _body() -> dict[str, Any]:
    return {
        "manifestDigest": "a" * 64,
        "evaluationDigest": "b" * 64,
        "modelProfileDigest": digest(model_profile()),
        "assertionId": "announcement",
        "componentPath": "src/form.ts",
        "componentName": "Form",
        "excerpts": [{"path": "src/form.ts", "lineStart": 1, "lineEnd": 3}],
        "supersedes": None,
        "billableCallAcknowledged": True,
    }


def test_reviewed_profile_matches_actual_worker_configuration() -> None:
    assert model_profile() == DiagnosisAgentProfile().model_dump(mode="json")
    validate(_body())


@pytest.mark.parametrize("fault", ["consent", "profile", "path", "scope", "lines", "extra"])
def test_request_cannot_widen_reviewed_scope(fault: str) -> None:
    body = _body()
    if fault == "consent":
        body["billableCallAcknowledged"] = "true"
    elif fault == "profile":
        body["modelProfileDigest"] = "c" * 64
    elif fault == "path":
        body["excerpts"][0]["path"] = "../private.env"
    elif fault == "scope":
        body["componentPath"] = "src/other.ts"
    elif fault == "lines":
        body["excerpts"][0]["lineStart"] = True
    else:
        body["requestedBy"] = "another-user"
    with pytest.raises(ValueError):
        validate(body)
