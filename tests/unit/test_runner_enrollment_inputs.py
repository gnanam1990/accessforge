"""Enrollment declaration types, not physical desktop qualification."""

from copy import deepcopy
from typing import Any

import pytest

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
