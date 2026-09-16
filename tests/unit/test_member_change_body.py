from typing import Any

import pytest

from accessforge_api.problems import ProblemDetail
from accessforge_api.routes.projects import _member_change_body


@pytest.mark.parametrize("role", ["OWNER", "MAINTAINER", "REVIEWER", "VIEWER", None])
def test_explicit_member_decision(role: str | None) -> None:
    assert _member_change_body({"role": role, "reason": " reviewed "}) == (role, "reviewed")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": "revoke"},
        {"role": False, "reason": "no coercion"},
        {"role": [], "reason": "no coercion"},
        {"role": "ADMIN", "reason": "unknown"},
        {"role": "OWNER", "reason": " "},
        {"role": None, "reason": "x" * 1001},
        {"role": None, "reason": "x", "workspaceId": "other"},
    ],
)
def test_incomplete_or_untyped_member_decision_refused(body: dict[str, Any]) -> None:
    with pytest.raises(ProblemDetail):
        _member_change_body(body)
