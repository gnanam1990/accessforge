from typing import Any

import pytest

from accessforge_api.problems import ProblemDetail
from accessforge_api.routes.membership_invitations import _offer_body


def test_identity_is_preserved_above_javascript_integer_precision() -> None:
    assert _offer_body(
        {
            "githubSubject": "9223372036854775806",
            "role": "REVIEWER",
            "ttlSeconds": 600,
            "reason": " reviewed ",
        }
    ) == (9223372036854775806, "REVIEWER", 600, "reviewed")


@pytest.mark.parametrize(
    "change",
    [
        {"githubSubject": 123},
        {"githubSubject": "0123"},
        {"githubSubject": "123.0"},
        {"githubSubject": "9223372036854775808"},
        {"githubSubject": "owner@example.test"},
        {"ttlSeconds": True},
        {"ttlSeconds": "600"},
        {"ttlSeconds": 59},
        {"ttlSeconds": 604801},
        {"role": []},
        {"role": "ADMIN"},
        {"reason": " "},
        {"workspaceId": "other"},
    ],
)
def test_invalid_offer_fields_are_not_coerced(change: dict[str, Any]) -> None:
    body = {"githubSubject": "123", "role": "VIEWER", "ttlSeconds": 600, "reason": "Review"}
    with pytest.raises(ProblemDetail):
        _offer_body({**body, **change})
