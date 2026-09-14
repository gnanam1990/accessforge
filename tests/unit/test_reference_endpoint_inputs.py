"""Reject invalid explicit endpoint inputs before touching any Docker runtime."""

from typing import Any

import pytest

from accessforge_build_worker.reference_regressions import ReferenceRegressions
from accessforge_build_worker.sandbox import SandboxRefused
from accessforge_build_worker.snapshot import SourceFile, SourceSnapshot


@pytest.mark.parametrize(
    "options",
    [
        {"endpoint_origin": "http://127.0.0.1:8081"},
        {"endpoint_fixture_nonce": "n" * 24},
        {"endpoint_fixture_nonce": "../escape", "on_candidate_endpoint": lambda _: None},
        {"endpoint_fixture_nonce": "short", "on_candidate_endpoint": lambda _: None},
    ],
)
def test_pinned_inputs_require_exact_fixture_and_explicit_session(options: dict[str, Any]) -> None:
    runner = object.__new__(ReferenceRegressions)
    # No sandbox exists: validation must finish before image inspection or process startup.
    with pytest.raises(SandboxRefused, match="pinned"):
        runner.run(SourceSnapshot((SourceFile("unused", b"not executed"),)), **options)
