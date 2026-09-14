"""Synthetic authenticated routing checks; not actual supervisor execution proof."""

from typing import Any, cast

import psycopg
import pytest

from accessforge_persistence import supervisor_effects as effects


@pytest.mark.parametrize(
    "baseline,candidate", [(True, False), (False, True), (False, False), (True, True)]
)
@pytest.mark.parametrize("operation", ["authorize_form", "status"])
def test_only_authenticated_original_binding_selects_effect_backend(
    monkeypatch: pytest.MonkeyPatch, baseline: bool, candidate: bool, operation: str
) -> None:
    order: list[str] = []

    class Connection:
        def execute(self, query: str, args: Any) -> "Connection":
            assert order == ["authenticated"]
            assert args == ("original-run", "original-run")
            return self

        def fetchone(self) -> dict[str, bool]:
            return dict(baseline=baseline, candidate=candidate)

    def live(*args: Any) -> tuple[dict[str, str], dict[str, Any]]:
        order.append("authenticated")
        return {"run_id": "original-run"}, {}

    def selected(name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> dict[str, str]:
            order.append(name)
            assert kwargs["token"] == "original-token"
            return {"selected": name}

        return call

    monkeypatch.setattr(effects.supervisor_sessions, "_live", live)
    for name, module in (
        (
            "baseline",
            effects.baseline_effects
            if operation == "authorize_form"
            else effects.baseline_effect_delivery,
        ),
        (
            "candidate",
            effects.candidate_effects
            if operation == "authorize_form"
            else effects.candidate_effect_delivery,
        ),
    ):
        monkeypatch.setattr(module, operation, selected(name))
    conn = cast(psycopg.Connection[dict[str, Any]], Connection())
    kwargs = dict(
        workspace_id="workspace", session_id="session", action_id="action", token="original-token"
    )
    if baseline == candidate:
        with pytest.raises(effects.supervisor_sessions.Refused):
            getattr(effects, operation)(conn, **kwargs)
        assert order == ["authenticated"]
    else:
        expected = "baseline" if baseline else "candidate"
        assert getattr(effects, operation)(conn, **kwargs) == {"selected": expected}
        assert order == ["authenticated", expected]
