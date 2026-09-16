"""Enrollment declaration types, not physical desktop qualification."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Response

from accessforge_api.routes.runners import _enrollment_identity
from accessforge_domain.runners.identity import EnrollmentError


def payload() -> dict[str, Any]:
    return {
        "token": "example-token",
        "name": "desk",
        "session": {
            "deviceId": "device-1",
            "platform": "darwin",
            "interactiveSessionId": "session-1",
            "console": False,
        },
        "profile": {
            "platform": "darwin",
            "readerName": "VoiceOver",
            "readerVersion": "14.0",
            "browserName": "Safari",
            "browserVersion": "26.0",
            "locale": "en-GB",
            "keyboardLayout": "ABC",
        },
    }


def test_console_boolean_preserved_and_changes_identity() -> None:
    body = payload()
    remote, _ = _enrollment_identity(body)
    body["session"]["console"] = True
    local, _ = _enrollment_identity(body)
    assert remote.console is False and local.console is True
    assert remote.key != local.key


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_console_coercion_refused(value: Any) -> None:
    body = payload()
    body["session"]["console"] = value
    with pytest.raises(EnrollmentError):
        _enrollment_identity(body)


def test_closed_complete_typed_identity() -> None:
    invalid_values: list[Any] = [None, 123, [], {}]
    for section, key in [("session", "deviceId"), ("profile", "readerVersion")]:
        for value in invalid_values:
            body = payload()
            body[section][key] = value
            with pytest.raises(EnrollmentError):
                _enrollment_identity(body)
        body = payload()
        del body[section][key]
        with pytest.raises(EnrollmentError):
            _enrollment_identity(body)
        body = payload()
        body[section]["unknown"] = True
        with pytest.raises(EnrollmentError):
            _enrollment_identity(body)
    for key in payload():
        body = deepcopy(payload())
        body[key] = None
        with pytest.raises(EnrollmentError):
            _enrollment_identity(body)


def test_enrollment_receipt_replay_does_not_redeem_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from accessforge_api.dependencies import IdempotentOutcome
    from accessforge_api.routes import runners as route

    context = SimpleNamespace(idempotency_key="same-operation", request_id="request")
    monkeypatch.setattr(route, "authorize", lambda *args: context)
    receipt = {"runnerId": "recorded", "status": "PREFLIGHT_REQUIRED", "profileDigest": "a" * 64}

    def replay(*args: Any, **kwargs: Any) -> IdempotentOutcome:
        assert kwargs["route"] == "POST /runners" and kwargs["body"] == payload()
        return IdempotentOutcome(replayed=True, response=receipt)

    monkeypatch.setattr(route, "run_idempotently", replay)
    monkeypatch.setattr(
        route.runners, "enroll_runner", lambda *args, **kwargs: pytest.fail("redeemed")
    )
    response = Response()
    assert (
        route.enroll_runner("workspace", cast(Any, None), response, cast(Any, None), payload())
        == receipt
    )
    assert response.headers["Idempotent-Replay"] == "true"
    assert response.headers["Cache-Control"] == "no-store"


def test_enrollment_authority_precedes_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    from accessforge_api.problems import ProblemCode, ProblemDetail
    from accessforge_api.routes import runners as route

    def denied(*args: Any) -> None:
        raise ProblemDetail(ProblemCode.PERMISSION_DENIED, "revoked")

    monkeypatch.setattr(route, "authorize", denied)
    monkeypatch.setattr(route, "run_idempotently", lambda *args, **kwargs: pytest.fail("replayed"))
    with pytest.raises(ProblemDetail):
        route.enroll_runner("workspace", cast(Any, None), Response(), cast(Any, None), payload())
