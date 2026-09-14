"""Synthetic original-binding decisions, not actual reader or functional execution proof."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest

from accessforge_domain.functional_validation import (
    INVALID_VALUES,
    VALIDATION_SUITE_DIGEST,
    ValidationObservation,
)
from accessforge_persistence import baseline_functional_assertions as producer
from accessforge_persistence import baseline_runs, journeys


@pytest.mark.parametrize(
    "fault", [None, "absent", "manifest", "epoch", "leased_run", "released_at", "prose"]
)
def test_only_original_released_reader_and_frozen_contract_bind_assertions(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    row: dict[str, Any] = dict(
        run_id="run",
        workspace_id="workspace",
        manifest_digest="original",
        run_manifest_digest="original",
        lease_id="lease",
        lease_epoch=1,
        actual_epoch=1,
        leased_run="run",
        released_at=datetime.now(UTC),
        canonical_manifest={"journeyVersionId": "journey", "assertionSetDigest": "assertions"},
    )
    if fault == "manifest":
        row["run_manifest_digest"] = "other"
    if fault == "epoch":
        row["actual_epoch"] = 2
    if fault == "leased_run":
        row["leased_run"] = "other"
    if fault == "released_at":
        row["released_at"] = None

    class Connection:
        def execute(self, query: str, args: Any) -> Any:
            value = (
                {"reviewer_summary": {} if fault == "prose" else {"assertionContract": {}}}
                if query.startswith("SELECT reviewer_summary")
                else None
                if fault == "absent"
                else row
            )
            return SimpleNamespace(fetchone=lambda: value)

    monkeypatch.setattr(baseline_runs, "assert_reader_released", lambda *a, **kw: None)
    contract = object()
    monkeypatch.setattr(journeys, "load_assertion_contract", lambda *a, **kw: contract)
    measurement = ValidationObservation(
        VALIDATION_SUITE_DIGEST, tuple((field, 422, 0) for field, _ in INVALID_VALUES)
    )

    def assertions(original: Any, observed: Any) -> list[dict[str, str]]:
        assert original is contract and observed is measurement
        return [{"assertionId": "validation", "condition": "TRUE"}]

    monkeypatch.setattr(producer, "functional_validation_assertions", assertions)
    conn = cast(psycopg.Connection[dict[str, Any]], Connection())
    if fault in {"manifest", "epoch", "leased_run", "released_at"}:
        with pytest.raises(ValueError):
            producer.receipt(conn, attempt_id="runtime", observation=measurement)
    else:
        result = producer.receipt(conn, attempt_id="runtime", observation=measurement)
        assert result["validation"] == measurement.canonical_form()
        if fault in {"absent", "prose"}:
            assert result["runEvidence"] is None
        else:
            assert result["runEvidence"]["leaseId"] == "lease"
            assert result["runEvidence"]["assertionSetDigest"] == "assertions"
