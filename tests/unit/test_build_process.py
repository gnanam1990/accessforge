"""Supervisor-owned command plumbing, not execution of customer source on the host."""

from __future__ import annotations

import sys
import time

import pytest

from accessforge_build_worker.process import CommandStopped, run_bounded


def test_command_input_and_both_output_streams_are_drained() -> None:
    result = run_bounded(
        (
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); "
            "sys.stderr.write('diagnostic'); sys.exit(7)",
        ),
        input_bytes=b"x" * 200_000,
        deadline=time.monotonic() + 5,
        output_limit=250_000,
    )
    assert result.code == 7
    assert result.stdout == b"x" * 200_000
    assert result.stderr == b"diagnostic"


def test_output_limit_stops_a_process_before_buffering_unbounded_data() -> None:
    with pytest.raises(CommandStopped, match="output limit"):
        run_bounded(
            (sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000)"),
            deadline=time.monotonic() + 5,
            output_limit=1024,
        )


def test_deadline_stops_a_process_that_never_exits() -> None:
    with pytest.raises(CommandStopped, match="deadline"):
        run_bounded(
            (sys.executable, "-c", "import time; time.sleep(20)"),
            deadline=time.monotonic() + 0.2,
            output_limit=1024,
        )


def test_cancellation_is_distinct_from_command_failure() -> None:
    with pytest.raises(CommandStopped, match="cancelled"):
        run_bounded(
            (sys.executable, "-c", "raise RuntimeError('must not run')"),
            deadline=time.monotonic() + 5,
            output_limit=1024,
            cancelled=lambda: True,
        )
