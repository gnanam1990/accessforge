"""Synthetic lifecycle wiring; not real service authentication or physical STOP proof."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from accessforge_domain.effect_monitor import EffectCoverage, EffectInterval, EffectWindow
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.reference_effect_scope import (
    REFERENCE_EFFECT_POLICY_DIGEST,
    reference_effect_scope_digest,
)
from accessforge_orchestrator import reference_effect_observer as subject
from accessforge_orchestrator.completion_observer import Refused
from accessforge_persistence import sequencer

RUN = "11111111-1111-4111-8111-111111111111"
ATTEMPT = "22222222-2222-4222-8222-222222222222"
INSTALLATION = "33333333-3333-4333-8333-333333333333"
WORKSPACE = "44444444-4444-4444-8444-444444444444"


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch) -> Any:
    calls: list[str] = []
    admitted: list[dict[str, Any]] = []
    context: dict[str, Any] = {
        "workspace": WORKSPACE,
        "run": RUN,
        "attempt": ATTEMPT,
        "lease": RUN,
        "epoch": 1,
        "manifestDigest": "a" * 64,
        "environmentConfigurationDigest": "b" * 64,
        "assertionSetDigest": "c" * 64,
        "fixtureId": INSTALLATION,
        "fixtureNonce": "synthetic-fixture-001",
        "producer": "observer:test",
        "afterActionSequence": 0,
        "lastAction": None,
        "lastResult": None,
        "assertionContract": AssertionSet(
            (
                Assertion(
                    "done",
                    AssertionKind.TASK_COMPLETION,
                    "Completion",
                    unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
                ),
                Assertion(
                    "none",
                    AssertionKind.FORBIDDEN_EFFECT,
                    "No request",
                    unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
                    evaluation_rule=EvaluationRule(
                        "CONTINUOUS_EFFECT_ABSENCE",
                        effect="CREATE_TEST_REQUEST",
                        scope_digest=REFERENCE_EFFECT_POLICY_DIGEST,
                    ),
                ),
            )
        ).canonical_form(),
    }

    class Connection:
        def execute(self, query: str, *_args: Any) -> Any:
            if "FROM producer_stream" in query:
                return SimpleNamespace(fetchone=lambda: {"admitted_through": len(admitted)})
            if "FROM canonical_event" in query:
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace(fetchone=lambda: None)

        def close(self) -> None:
            calls.append("connection-close")

    @contextmanager
    def workspace(*_args: Any) -> Any:
        yield Connection()

    def actual_context(*_args: Any, before_dispatch: bool) -> dict[str, Any]:
        calls.append("startup-context" if before_dispatch else "stop-context")
        return deepcopy(context)

    class Collector:
        def __init__(self, _connection: Any, **kwargs: Any) -> None:
            self.options = kwargs

        def begin(self) -> int:
            calls.append("collector-begin")
            return 10

        def finish(self) -> EffectCoverage:
            calls.append("collector-finish")
            target = EffectWindow(
                RUN,
                ATTEMPT,
                reference_effect_scope_digest(INSTALLATION, context["fixtureNonce"]),
                self.options["clock_epoch"],
                "CREATE_TEST_REQUEST",
                10,
                30,
            )
            return EffectCoverage(target, (EffectInterval(10, 30, 1),))

        def abort(self) -> None:
            calls.append("collector-abort")

    def admit(_conn: Any, **kwargs: Any) -> Any:
        calls.append("retain-" + kwargs["payload"]["sourceRecord"]["collectorPhase"])
        admitted.append(kwargs)
        return SimpleNamespace(event_id=str(len(admitted)))

    monkeypatch.setattr(subject, "workspace_connection", workspace)
    monkeypatch.setattr(subject, "_context", actual_context)
    monkeypatch.setattr(subject, "ReferenceEffectCollector", Collector)
    monkeypatch.setattr(psycopg, "connect", lambda *_a, **_kw: Connection())
    monkeypatch.setattr(sequencer, "admit_record", admit)
    observer = subject.ReferenceEffectObserver(
        "dbname=synthetic-product",
        "dbname=synthetic-reference",
        workspace_id=WORKSPACE,
        run_id=RUN,
        credential_ref="observer-profile",
        application_role="app",
        installation_id=INSTALLATION,
        expected_attempt_id=ATTEMPT,
    )
    return SimpleNamespace(
        observer=observer, calls=calls, admitted=admitted, context=context, collector=Collector
    )


def stopped(wired: Any) -> None:
    wired.context.update(afterActionSequence=2, lastAction="STOP", lastResult="SUCCEEDED")


def test_ready_precedes_dispatch_and_closed_follows_rechecked_stop(wired: Any) -> None:
    assert wired.observer.begin() == "1"
    assert wired.calls == ["startup-context", "collector-begin", "startup-context", "retain-READY"]
    stopped(wired)
    assert wired.observer.finish() == "2"
    assert wired.calls[-4:] == ["stop-context", "collector-finish", "stop-context", "retain-CLOSED"]
    ready, closed = [item["payload"]["sourceRecord"] for item in wired.admitted]
    assert closed["startEventId"] == "1"
    assert ready["startNs"] == closed["startNs"] == "10"
    assert closed["intervals"] == [{"startNs": "10", "endNs": "30", "occurrences": "1"}]
    assert closed["finalSample"] is False and closed["count"] is None
    assert ready["assertionObservations"] == []
    assert closed["conditionFormat"] == "accessforge.reference-effect-conditions.v1"
    assert closed["assertionObservations"] == [
        {
            "assertionId": "none",
            "kind": "FORBIDDEN_EFFECT",
            "condition": "FALSE",
            "provenance": "OBSERVER_AUTHORED",
        }
    ]
    assert "synthetic-fixture-001" not in str(wired.admitted)
    assert [item["producer_sequence"] for item in wired.admitted] == [1, 2]


def test_finish_without_original_stop_never_closes_collector_or_retains(wired: Any) -> None:
    wired.observer.begin()
    with pytest.raises(Refused):
        wired.observer.finish()
    assert "collector-finish" not in wired.calls
    assert len(wired.admitted) == 1
    assert wired.calls[-1] == "collector-abort"


def test_changed_identity_refuses_closure(wired: Any) -> None:
    wired.observer.begin()
    stopped(wired)
    wired.context["manifestDigest"] = "d" * 64
    with pytest.raises(Refused):
        wired.observer.finish()
    assert "collector-finish" not in wired.calls


def test_wrong_original_attempt_is_refused_before_collector_start(wired: Any) -> None:
    wired.context["attempt"] = WORKSPACE
    with pytest.raises(Refused):
        wired.observer.begin()
    assert wired.calls == ["startup-context"]


def test_unmatched_frozen_policy_does_not_open_the_collector(wired: Any) -> None:
    wired.context["assertionContract"] = None
    with pytest.raises(Refused):
        wired.observer.begin()
    assert wired.calls == ["startup-context"]


def test_a_second_begin_aborts_instead_of_replaying(wired: Any) -> None:
    wired.observer.begin()
    with pytest.raises(Refused):
        wired.observer.begin()
    assert wired.calls.count("collector-begin") == 1
    assert wired.calls[-1] == "collector-abort"


def test_foreign_coverage_is_not_retained(wired: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    original = wired.collector.finish

    def foreign(self: Any) -> EffectCoverage:
        measured = original(self)
        assert isinstance(measured, EffectCoverage)
        return replace(measured, window=replace(measured.window, clock_epoch=WORKSPACE))

    monkeypatch.setattr(wired.collector, "finish", foreign)
    wired.observer.begin()
    stopped(wired)
    with pytest.raises(Refused):
        wired.observer.finish()
    assert len(wired.admitted) == 1


def test_late_action_during_closure_refuses_the_collected_result(
    wired: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = wired.collector.finish

    def changed(self: Any) -> EffectCoverage:
        measured = original(self)
        assert isinstance(measured, EffectCoverage)
        wired.context["afterActionSequence"] = 3
        return measured

    monkeypatch.setattr(wired.collector, "finish", changed)
    wired.observer.begin()
    stopped(wired)
    with pytest.raises(Refused):
        wired.observer.finish()
    assert len(wired.admitted) == 1


def test_uncertain_ready_retention_cannot_restart_collector(
    wired: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def uncertain(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic uncertain commit")

    monkeypatch.setattr(sequencer, "admit_record", uncertain)
    with pytest.raises(Refused):
        wired.observer.begin()
    with pytest.raises(Refused):
        wired.observer.begin()
    assert wired.calls.count("collector-begin") == 1
    assert wired.calls[-1] == "collector-abort"
