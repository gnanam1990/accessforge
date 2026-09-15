"""Synthetic controller composition; original database admission and native startup are separate."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest

from accessforge_build_worker.baseline_session import BaselineSession
from accessforge_orchestrator import baseline_reader_dispatch as dispatch
from accessforge_orchestrator import manual_dispatch
from accessforge_persistence import baseline_runs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "transport", "admission", "binding", "read-commit", "dispatch"]
)
async def test_only_original_committed_reader_identity_is_dispatched(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events: list[str] = []

    class Transport:
        def check_available(self) -> None:
            events.append("transport")
            if failure == "transport":
                raise RuntimeError("synthetic transport unavailable")

        async def start(self, reference: Any, *, ticket: Any) -> None:
            raise AssertionError("only the real manual controller may call transport.start")

    def admit(**kwargs: Any) -> Any:
        assert kwargs == dict(runner_id="runner", attempt_id="attempt")
        events.append("admission-committed")
        if failure == "admission":
            raise RuntimeError("synthetic lost admission acknowledgement")
        return SimpleNamespace(lease_id="original-lease", epoch=7)

    class Connection:
        def execute(self, query: str, args: Any) -> Any:
            assert args == ("runtime", "workspace", "original-lease", "runner", "attempt", 7)
            assert "l.run_id=r.id AND l.epoch=b.lease_epoch" in query
            return SimpleNamespace(
                fetchone=lambda: (
                    None
                    if failure == "binding"
                    else dict(
                        run_id="original-run",
                        revision=12,
                        attempt_id="attempt",
                        runner_id="runner",
                        lease_id="original-lease",
                        epoch=7,
                    )
                )
            )

    @contextmanager
    def connection(database: str, workspace: str) -> Any:
        assert (database, workspace) == ("database", "workspace")
        events.append("binding")
        yield Connection()
        if failure == "read-commit":
            raise RuntimeError("synthetic binding read closure failure")
        events.append("binding-closed")

    async def deliver(self: Any, reference: Any, *, expected_revision: int) -> Any:
        assert expected_revision == 12
        assert reference == manual_dispatch.DispatchReference(
            "workspace", "original-run", "attempt", "runner", "original-lease", 7
        )
        events.append("dispatch")
        if failure == "dispatch":
            raise manual_dispatch.HandoffUnknown("synthetic unknown delivery")
        return reference

    monkeypatch.setattr(dispatch, "workspace_connection", connection)
    monkeypatch.setattr(manual_dispatch.ManualRunController, "dispatch", deliver)
    session = cast(
        BaselineSession,
        SimpleNamespace(
            database_url="database",
            workspace_id="workspace",
            claim=SimpleNamespace(attempt_id="runtime"),
            admit_reader=admit,
        ),
    )
    if failure is None:
        result = await dispatch.admit_and_dispatch_reader(
            session, runner_id="runner", attempt_id="attempt", transport=Transport()
        )
        assert result.run_id == "original-run"
    else:
        with pytest.raises((RuntimeError, baseline_runs.Refused)):
            await dispatch.admit_and_dispatch_reader(
                session, runner_id="runner", attempt_id="attempt", transport=Transport()
            )
    expected = ["transport", "admission-committed", "binding", "binding-closed", "dispatch"]
    stops = {"transport": 1, "admission": 2, "binding": 3, "read-commit": 3, "dispatch": 5}
    assert events == expected[: stops[failure] if failure else 5]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, float("nan"), 31, 10])
async def test_invalid_configuration_and_default_transport_never_admit(timeout: float) -> None:
    # Even an unusable session must not be inspected before rejecting unconfigured dispatch.
    with pytest.raises((ValueError, manual_dispatch.ReaderTransportUnavailable)):
        await dispatch.admit_and_dispatch_reader(
            cast(BaselineSession, None),
            runner_id="runner",
            attempt_id="attempt",
            acknowledgement_timeout_seconds=timeout,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stopped", "pending", "timeout", "missing", "cancelled"])
async def test_dispatch_ack_waits_for_original_stop_without_replay(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    deliveries = 0
    reads = 0
    reference = manual_dispatch.DispatchReference(
        "workspace", "run", "attempt", "runner", "lease", 1
    )

    async def deliver(*args: Any, **kwargs: Any) -> Any:
        nonlocal deliveries
        deliveries += 1
        if mode == "cancelled":
            raise asyncio.CancelledError
        return reference

    async def stopped(*args: Any) -> bool:
        nonlocal reads
        reads += 1
        if mode == "missing":
            raise baseline_runs.Refused("original binding absent")
        return mode == "stopped" or (mode == "pending" and reads > 1)

    monkeypatch.setattr(dispatch, "admit_and_dispatch_reader", deliver)
    monkeypatch.setattr(dispatch, "_reader_stopped", stopped)
    kwargs: dict[str, Any] = dict(runner_id="runner", attempt_id="attempt", timeout_seconds=0.3)
    if mode in {"stopped", "pending"}:
        assert (
            await dispatch.admit_dispatch_and_wait_reader(cast(BaselineSession, None), **kwargs)
            == reference
        )
    else:
        exception = {
            "timeout": manual_dispatch.HandoffUnknown,
            "missing": baseline_runs.Refused,
            "cancelled": asyncio.CancelledError,
        }[mode]
        with pytest.raises(exception):
            await dispatch.admit_dispatch_and_wait_reader(cast(BaselineSession, None), **kwargs)
    assert deliveries == 1
    assert reads == 0 if mode == "cancelled" else reads >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, True, float("nan"), float("inf"), 61])
async def test_stop_wait_rejects_invalid_bounds_before_admission(timeout: float) -> None:
    with pytest.raises(ValueError):
        await dispatch.admit_dispatch_and_wait_reader(
            cast(BaselineSession, None),
            runner_id="runner",
            attempt_id="attempt",
            timeout_seconds=timeout,
        )


@pytest.mark.asyncio
async def test_stop_query_cancellation_closes_connection_without_executor_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Connection:
        async def execute(self, query: str, args: Any = None) -> Any:
            if "set_config" in query:
                assert args == ("workspace",)
                events.append("workspace")
            if "FROM baseline_session_binding" in query:
                events.append("query")
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("query-cancelled")

        async def close(self) -> None:
            events.append("closed")

    async def connect(database_url: str, **kwargs: Any) -> Any:
        assert database_url == "private-database" and kwargs["connect_timeout"] == 2
        return Connection()

    def no_executor(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("STOP polling must not leave an executor worker behind")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(asyncio, "to_thread", no_executor)
    session = cast(
        BaselineSession,
        SimpleNamespace(
            database_url="private-database",
            workspace_id="workspace",
            claim=SimpleNamespace(attempt_id="runtime"),
        ),
    )
    reference = manual_dispatch.DispatchReference(
        "workspace", "run", "attempt", "runner", "lease", 1
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(dispatch._reader_stopped(session, reference), timeout=0.02)
    assert events == ["workspace", "query", "query-cancelled", "closed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before", "dispatch", "query"])
async def test_operator_cancellation_is_not_stop_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    cancelled = stage == "before"
    calls: list[str] = []
    reference = manual_dispatch.DispatchReference(
        "workspace", "run", "attempt", "runner", "lease", 1
    )

    async def deliver(*args: Any, **kwargs: Any) -> Any:
        nonlocal cancelled
        calls.append("dispatch")
        cancelled = stage == "dispatch"
        return reference

    async def stopped(*args: Any) -> bool:
        nonlocal cancelled
        calls.append("query")
        cancelled = True
        return True

    monkeypatch.setattr(dispatch, "admit_and_dispatch_reader", deliver)
    monkeypatch.setattr(dispatch, "_reader_stopped", stopped)
    error = baseline_runs.Refused if stage == "before" else manual_dispatch.HandoffUnknown
    with pytest.raises(error):
        await dispatch.admit_dispatch_and_wait_reader(
            cast(BaselineSession, None),
            runner_id="runner",
            attempt_id="attempt",
            cancelled=lambda: cancelled,
        )
    assert calls == {"before": [], "dispatch": ["dispatch"], "query": ["dispatch", "query"]}[stage]
