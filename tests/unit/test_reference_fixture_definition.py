"""Logical fixture semantics are versioned separately from presentation/source identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from accessforge_contracts.reference_fixture import (
    REFERENCE_FIXTURE_DIGEST,
    REFERENCE_FIXTURE_VERSION,
)
from accessforge_domain.canonical import digest
from reference_app import fixture_contract, templates, validation
from reference_app.fixture_definition import template_digest

ROOT = Path(__file__).resolve().parents[2]


def definition() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(
        (ROOT / "packages/contracts/fixtures/reference-service-request-v1.json").read_text()
    )
    return value


def test_v1_definition_has_an_independent_fixed_canonical_digest() -> None:
    expected = "39acd4e6ff833c3f5668cbc951f541658858568bd9ba31814bfb19a318dbb6a3"
    assert digest(definition()) == expected == REFERENCE_FIXTURE_DIGEST
    assert fixture_contract.REFERENCE_FIXTURE_DIGEST == expected
    assert definition()["identityVersion"] == REFERENCE_FIXTURE_VERSION
    assert fixture_contract.REFERENCE_FIXTURE_VERSION == REFERENCE_FIXTURE_VERSION


def test_declared_field_rules_match_the_protected_backend() -> None:
    fields = definition()["fields"]
    assert fields["full_name"]["maxLength"] == validation.MAX_NAME
    assert fields["description"]["minLength"] == validation.MIN_DESCRIPTION
    assert fields["description"]["maxLength"] == validation.MAX_DESCRIPTION
    assert tuple(fields["category"]["values"]) == validation.ALLOWED_CATEGORIES
    assert fields["email"]["pattern"] == validation._EMAIL.pattern


def test_ui_change_does_not_change_logical_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    original = templates.render_form
    before = templates.presentation_digest("inaccessible")

    def changed(**kwargs: Any) -> str:
        return original(**kwargs).replace("Service request", "Updated request presentation")

    monkeypatch.setattr(templates, "render_form", changed)
    assert templates.presentation_digest("inaccessible") != before
    assert template_digest("inaccessible") == REFERENCE_FIXTURE_DIGEST
    assert template_digest("accessible") == REFERENCE_FIXTURE_DIGEST


@pytest.mark.parametrize(
    "field", ["identityVersion", "fields", "submission", "authorization", "freshNoncePerRun"]
)
def test_semantic_contract_changes_cannot_reuse_the_v1_digest(field: str) -> None:
    changed = definition()
    changed[field] = {"changed": True}
    assert digest(changed) != REFERENCE_FIXTURE_DIGEST


@pytest.mark.parametrize("variant", ["", "unknown", "../accessible"])
def test_unknown_presentation_does_not_get_a_valid_fixture_identity(variant: str) -> None:
    with pytest.raises(ValueError, match="unknown"):
        template_digest(variant)
    with pytest.raises(ValueError, match="unknown"):
        templates.presentation_digest(variant)


def test_ci_requires_generated_fixture_identity_drift_check() -> None:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    matches = [
        step
        for job in jobs.values()
        for step in job.get("steps", [])
        if step.get("run") == "uv run python scripts/generate_reference_fixture.py --check"
    ]
    assert len(matches) == 1
    assert "if" not in matches[0] and not matches[0].get("continue-on-error", False)
