"""Synthetic controller handoff decisions, no native startup or lease admission proof."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from accessforge_build_worker import baseline_session as handoff
from accessforge_build_worker.candidate_gateway import CandidateGateway
from accessforge_persistence import baseline_runs, runners
from accessforge_persistence.candidate_regressions import RegressionClaim


@pytest.mark.parametrize("seconds,ttl", [(0, None), (1.9, None), (2.9, 1), (32.9, 31), (120, 60)])
@pytest.mark.parametrize("lost_commit", [False, True])
def test_original_lifetime_and_commit_bound_reader_handoff(
    monkeypatch: pytest.MonkeyPatch, seconds: float, ttl: int | None, lost_commit: bool
) -> None:
    events: list[str] = []
    lease = runners.AdmittedLease(
        "original-lease", "runner", "session-key", 1, "2026-09-14T00:00:00Z"
    )

    class Connection:
        def execute(self, query: str, args: Any) -> Any:
            value = (
                {"run_id": "original-run"}
                if query.startswith("SELECT run_id")
                else {"seconds": seconds}
            )
            return SimpleNamespace(fetchone=lambda: value)

    @contextmanager
    def connection(*args: Any) -> Any:
        yield Connection()
        if lost_commit:
            raise RuntimeError("synthetic commit acknowledgement lost")
        events.append("commit")

    def admit(conn: Any, **kwargs: Any) -> Any:
        assert kwargs == dict(
            workspace_id="workspace",
            runner_id="runner",
            run_id="original-run",
            attempt_id="attempt",
            ttl_seconds=ttl,
        )
        events.append("admitted")
        return lease

    monkeypatch.setattr(handoff, "workspace_connection", connection)
    monkeypatch.setattr(
        baseline_runs, "assert_live", lambda *a, **kw: {"regression_attempt_id": "runtime"}
    )
    monkeypatch.setattr(runners, "admit_lease", admit)
    gateway = cast(CandidateGateway, SimpleNamespace(receipt=lambda: events.append("gateway")))
    session = handoff.BaselineSession(
        "private-database-secret",
        "workspace",
        RegressionClaim("runtime", "build", "private-worker-secret", 1),
        gateway,
    )
    assert "private" not in repr(session)
    if ttl is None:
        with pytest.raises(baseline_runs.Refused):
            session.admit_reader(runner_id="runner", attempt_id="attempt")
        assert events == ["gateway"]
    elif lost_commit:
        with pytest.raises(RuntimeError, match="commit acknowledgement"):
            session.admit_reader(runner_id="runner", attempt_id="attempt")
        assert events == ["gateway", "admitted"]
    else:
        assert session.admit_reader(runner_id="runner", attempt_id="attempt") == lease
        assert events == ["gateway", "admitted", "commit"]
