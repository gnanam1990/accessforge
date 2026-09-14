"""Synthetic controller composition; original database admission and native startup are separate."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

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
