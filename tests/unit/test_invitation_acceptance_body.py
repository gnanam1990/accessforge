from typing import Any

import pytest

from accessforge_api.problems import ProblemDetail
from accessforge_api.routes.invitation_acceptance import _decision


@pytest.mark.parametrize("header", ["1", '"1"'])
def test_explicit_acceptance(header: str) -> None:
    assert _decision({"accept": True}, header) == 1


@pytest.mark.parametrize(
    "body,header",
    [
        ({}, "1"),
        ({"accept": 1}, "1"),
        ({"accept": "true"}, "1"),
        ({"accept": False}, "1"),
        ({"accept": True, "githubSubject": "123"}, "1"),
        ({"accept": True, "userId": "caller"}, "1"),
        ({"accept": True}, None),
        ({"accept": True}, "0"),
        ({"accept": True}, "2"),
        ({"accept": True}, "*"),
        ({"accept": True}, 'W/"1"'),
    ],
)
def test_acceptance_rejects_forged_identity_coercion_and_stale_revision(
    body: dict[str, Any], header: str | None
) -> None:
    with pytest.raises(ProblemDetail):
        _decision(body, header)
