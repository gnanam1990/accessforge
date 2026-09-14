"""Composition ordering only; no real reader, container, DB or S3 acceptance."""

from typing import Any

import pytest

from accessforge_orchestrator import baseline_completion as composition
from accessforge_persistence import baseline_builds


@pytest.mark.parametrize(
    "fault",
    [None, "reader", "runtime-close", "missing-reader", "duplicate", "retention", "configuration"],
)
def test_completion_follows_original_reader_and_runtime_closure(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    events: list[str] = []
    snapshot = {"snapshot": {"outcome": "INCONCLUSIVE"}}
    archive, evidence, session = object(), object(), object()

    def reader(value: Any) -> None:
        assert value is session
        events.append("reader")
        if fault == "reader":
            raise RuntimeError("reader unconfirmed")

    def runtime(database_url: str, **kwargs: Any) -> None:
        assert database_url == "explicit-database"
        assert kwargs["run_id"] == "original-run" and kwargs["build_id"] == "original-build"
        assert kwargs["store"] is archive
        assert kwargs["reset_credential_ref"] == "reset-ref"
        assert kwargs["observer_credential_ref"] == "observer-ref"
        events.append("runtime")
        if fault == "missing-reader":
            return
        kwargs["on_session"](session)
        if fault == "duplicate":
            kwargs["on_session"](session)
        if fault == "runtime-close":
            raise RuntimeError("runtime closure unconfirmed")
        events.append("runtime-receipt-committed")

    def complete(database_url: str, store: Any, **kwargs: Any) -> dict[str, Any]:
        assert events == ["runtime", "reader", "runtime-receipt-committed"]
        assert database_url == "explicit-database" and store is evidence
        assert kwargs == {
            "workspace_id": "workspace",
            "run_id": "original-run",
            "journal_path": "private-spool",
        }
        events.append("complete")
        if fault == "retention":
            raise RuntimeError("retention unconfirmed")
        return snapshot

    monkeypatch.setattr(composition, "execute_baseline_session", runtime)
    monkeypatch.setattr(composition, "complete", complete)
    arguments: dict[str, Any] = dict(
        workspace_id="workspace",
        run_id="original-run",
        build_id="original-build",
        origin="http://127.0.0.1:8081",
        reset_credential_ref="reset-ref",
        observer_credential_ref="observer-ref",
        runner=object(),
        archive_store=archive,
        evidence_store=evidence,
        journal_path="private-spool",
        on_session=reader,
    )
    if fault == "configuration":
        arguments["on_session"] = None
    if fault is None:
        assert composition.execute_and_complete("explicit-database", **arguments) is snapshot
    else:
        with pytest.raises((RuntimeError, baseline_builds.Refused)):
            composition.execute_and_complete("explicit-database", **arguments)
    assert events.count("runtime") == int(fault != "configuration")
    assert events.count("reader") <= 1
    assert ("complete" in events) is (fault in (None, "retention"))
