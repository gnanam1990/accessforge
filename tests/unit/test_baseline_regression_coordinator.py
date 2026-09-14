"""Synthetic runner ordering; actual HTTP/database observations are not produced here."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from accessforge_build_worker import baseline_regression_coordinator as coordinator
from accessforge_build_worker.artifacts import CandidateArchiveStore
from accessforge_build_worker.reference_regressions import (
    ReferenceRegressionResult,
    ReferenceRegressions,
)
from accessforge_build_worker.sandbox import CleanupUnconfirmed, DaemonBinding
from accessforge_build_worker.snapshot import SourceFile, SourceSnapshot
from accessforge_domain.functional_validation import (
    INVALID_VALUES,
    VALIDATION_SUITE_DIGEST,
    ValidationObservation,
)
from accessforge_persistence import baseline_effect_delivery, baseline_observations, baseline_runs
from accessforge_persistence import baseline_endpoints as endpoints
from accessforge_persistence import baseline_regressions as regressions
from accessforge_persistence.candidate_regressions import ROLES, RegressionClaim


@pytest.mark.parametrize(
    "fault", [None, "active", "cleanup", "finish", "session", "session-commit"]
)
def test_baseline_runtime_commits_facts_then_rechecks_before_activation(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    events: list[str] = []
    artifact = SourceSnapshot((SourceFile("out/fixture", b"synthetic"),))
    daemon = DaemonBinding("unix:///fixture/docker.sock", "fixture")
    image = "sha256:" + "a" * 64
    claim = RegressionClaim("task", "build", "token", 1)
    validation = ValidationObservation(
        VALIDATION_SUITE_DIGEST, tuple((field, 422, 0) for field, _ in INVALID_VALUES)
    )
    result = ReferenceRegressionResult(
        artifact.archive_digest,
        "task",
        daemon,
        ("synthetic",),
        tuple((role, "b" * 64, image) for role in ROLES),
        validation,
    )

    @contextmanager
    def connection(*args: Any) -> Iterator[object]:
        events.append("open")
        yield object()
        if fault == "session-commit" and events[-1] == "consume":
            raise regressions.Refused("synthetic lost commit acknowledgement")
        events.append("commit")

    def stage(name: str) -> Any:
        def apply(*args: Any, **kwargs: Any) -> None:
            events.append(name)
            if fault == name:
                raise regressions.Refused(name)

        return apply

    def run(*args: Any, **kwargs: Any) -> ReferenceRegressionResult:
        assert events[-3:] == ["open", "dispatch", "commit"]
        if fault in {"session", "session-commit"}:
            assert kwargs["endpoint_origin"] == "http://127.0.0.1:8081"
            assert kwargs["endpoint_fixture_nonce"] == "original-fixture-nonce"
            gateway = SimpleNamespace(receipt=stage("receipt"))
            kwargs["on_candidate_endpoint"](gateway)
            assert events[-5:] == ["receipt", "open", "prepare", "commit", "reader"]
            kwargs["on_endpoint_planned"]({})
            kwargs["on_endpoint_bound"]({})
            kwargs["on_artifact_observed"]({})
            assert events[-3:] == ["open", "observed", "commit"]
            kwargs["begin_candidate_effect"]("/form/original", "")
            assert events[-3:] == ["open", "consume", "commit"]
            kwargs["assert_candidate_effect"]({})
            kwargs["on_candidate_effect_response"]({}, {})
            assert events[-3:] == ["open", "response", "commit"]
            kwargs["on_endpoint_closed"](True)
            assert events[-3:] == ["open", "closed", "commit"]
        for role in ROLES:
            kwargs["on_planned"](role, "name", image)
            assert events[-3:] == ["open", "planned", "commit"]
            try:
                kwargs["on_created"](role, "b" * 64, image)
                assert events[-6:] == ["open", "created", "commit", "open", "active", "commit"]
            finally:
                kwargs["on_removed"](role)
        if fault == "cleanup":
            raise CleanupUnconfirmed("synthetic uncertain process")
        return result

    def fail(*args: Any, **kwargs: Any) -> None:
        assert kwargs["cleanup_confirmed"] is (fault != "cleanup")
        events.append("fail")

    runner = SimpleNamespace(
        image=image,
        policy_digest=lambda: "p" * 64,
        run=run,
        sandbox=SimpleNamespace(
            daemon=daemon, _checked=lambda *a, **kw: SimpleNamespace(stdout=image.encode())
        ),
    )
    monkeypatch.setattr(coordinator, "workspace_connection", connection)
    monkeypatch.setattr(coordinator, "read_retained_baseline", lambda *a, **kw: artifact)
    monkeypatch.setattr(regressions, "claim", lambda *a, **kw: claim)
    for name in ("dispatch", "planned", "created", "removed", "finish"):
        monkeypatch.setattr(regressions, name, stage(name))
    monkeypatch.setattr(regressions, "assert_active", stage("active"))
    monkeypatch.setattr(regressions, "fail", fail)
    monkeypatch.setattr(baseline_runs, "prepare", stage("prepare"))
    for method in ("plan", "bound", "closed"):
        monkeypatch.setattr(endpoints, method, stage(method))
    monkeypatch.setattr(baseline_observations, "retain", stage("observed"))
    monkeypatch.setattr(baseline_effect_delivery, "begin", stage("consume"))
    monkeypatch.setattr(baseline_effect_delivery, "check", stage("check"))
    monkeypatch.setattr(baseline_effect_delivery, "retain_response", stage("response"))
    args: dict[str, Any] = dict(
        workspace_id="ws",
        build_id="build",
        runner=cast(ReferenceRegressions, runner),
        store=cast(CandidateArchiveStore, object()),
    )
    if fault in {"session", "session-commit"}:
        args.update(
            on_baseline_session=lambda gateway: events.append("reader"),
            endpoint_origin="http://127.0.0.1:8081",
            endpoint_fixture_nonce="original-fixture-nonce",
            reserve_fixture=lambda claim, nonce: "original-context",
            confirm_fixture=lambda claim, fingerprint, application: None,
        )
    if fault in {None, "session"}:
        assert coordinator.execute_baseline_regressions("unused", **args) == result
        assert events[-3:] == ["open", "finish", "commit"]
    else:
        with pytest.raises((regressions.Refused, CleanupUnconfirmed)):
            coordinator.execute_baseline_regressions("unused", **args)
        assert events[-3:] == ["open", "fail", "commit"]
        if fault == "active":
            assert events.count("created") == 1 and "finish" not in events
        if fault == "session-commit":
            assert "check" not in events and "response" not in events and "finish" not in events
