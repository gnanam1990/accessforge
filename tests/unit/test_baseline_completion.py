"""Composition ordering only; no real reader, container, DB or S3 acceptance."""

import asyncio
from typing import Any

import pytest

from accessforge_orchestrator import baseline_completion as composition
from accessforge_orchestrator.manual_dispatch import HandoffUnknown, ReaderTransportUnavailable
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


@pytest.mark.parametrize("fault", [None, "stop", "unavailable", "cancelled", "timeout", "loop"])
def test_operator_dispatches_once_and_only_completes_after_stop(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    events: list[str] = []
    session = object()

    class Transport:
        def check_available(self) -> None:
            if fault == "unavailable":
                raise ReaderTransportUnavailable("unqualified")

    transport = Transport()

    async def dispatch(value: Any, **kwargs: Any) -> None:
        assert value is session
        assert kwargs == {
            "runner_id": "runner",
            "attempt_id": "attempt",
            "transport": transport,
            "timeout_seconds": 30,
            "cancelled": arguments["cancelled"],
        }
        events.append("dispatch")
        if fault == "stop":
            raise HandoffUnknown("original STOP unconfirmed")
        events.append("stop")

    def runtime(database_url: str, **kwargs: Any) -> None:
        assert database_url == "explicit-database"
        events.append("runtime")
        kwargs["on_session"](session)
        events.append("closed")

    def complete(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert events == ["runtime", "dispatch", "stop", "closed"]
        events.append("complete")
        return {"outcome": "INCONCLUSIVE"}

    monkeypatch.setattr(composition, "admit_dispatch_and_wait_reader", dispatch)
    monkeypatch.setattr(composition, "execute_baseline_session", runtime)
    monkeypatch.setattr(composition, "complete", complete)
    arguments: dict[str, Any] = dict(
        workspace_id="workspace",
        run_id="run",
        build_id="build",
        origin="http://127.0.0.1:8081",
        reset_credential_ref="reset",
        observer_credential_ref="observer",
        runner=object(),
        archive_store=object(),
        evidence_store=object(),
        journal_path="private-spool",
        runner_id="runner",
        attempt_id="attempt",
        transport=transport,
        timeout_seconds=0 if fault == "timeout" else 30,
        cancelled=lambda: fault == "cancelled",
    )

    def invoke() -> dict[str, Any]:
        return composition.dispatch_and_complete("explicit-database", **arguments)

    if fault is None:
        assert invoke() == {"outcome": "INCONCLUSIVE"}
        assert events[-1] == "complete"
    elif fault == "loop":

        async def inside_loop() -> None:
            with pytest.raises(ValueError, match="active event loop"):
                invoke()

        asyncio.run(inside_loop())
    else:
        expected_exception = {
            "stop": HandoffUnknown,
            "unavailable": ReaderTransportUnavailable,
            "cancelled": baseline_builds.Refused,
            "timeout": ValueError,
        }[fault]
        with pytest.raises(expected_exception):
            invoke()
    if fault == "stop":
        assert events == ["runtime", "dispatch"]
    elif fault is not None:
        assert events == []
