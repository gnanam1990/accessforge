"""Real pipe framing with a synthetic observer; no database, provider or desktop effects."""

import json
import os
import select
import signal
import subprocess
import sys
from typing import Any

import pytest

from accessforge_orchestrator import reference_effect_worker as worker

READY = "11111111-1111-4111-8111-111111111111"
CLOSED = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"


def start() -> dict[str, Any]:
    return {
        "protocol": worker.PROTOCOL,
        "workspaceId": WORKSPACE,
        "runId": READY,
        "attemptId": CLOSED,
        "observerCredentialRef": "private-observer",
        "applicationRole": "private-app-role",
        "installationId": CLOSED,
        "maxWallSeconds": 10,
    }


def line(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"protocol": "other"},
        {"maxWallSeconds": True},
        {"maxWallSeconds": 0},
        {"maxWallSeconds": 1801},
        {"workspaceId": "invalid"},
        {"applicationRole": "line\nbreak"},
        {"observerCredentialRef": ""},
    ],
)
def test_closed_start_contract(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        worker.parse_start(line({**start(), **change}))


def test_duplicate_keys_and_multiple_records_are_not_accepted() -> None:
    original = line(start())
    assert worker.parse_start(original) == start()
    for data in (
        original + original,
        original[:-1],
        b"x" * 4097,
        original.replace(b'"maxWallSeconds":10', b'"maxWallSeconds":10,"maxWallSeconds":10'),
    ):
        with pytest.raises(ValueError):
            worker.parse_start(data)


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "wrong-ready",
        "eof",
        "extra-command",
        "missing-env",
        "begin-failure",
        "finish-failure",
    ],
)
def test_private_lifecycle(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Observer:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("construct")

        def begin(self) -> str:
            calls.append("begin")
            if case == "begin-failure":
                raise ValueError("synthetic unavailable")
            return READY

        def finish(self) -> str:
            calls.append("finish")
            if case == "finish-failure":
                raise ValueError("synthetic STOP not confirmed")
            return CLOSED

        def abort(self) -> None:
            calls.append("abort")

    monkeypatch.setattr(worker, "ReferenceEffectObserver", Observer)
    incoming, sender = os.pipe()
    receiver, outgoing = os.pipe()
    try:
        command = line(
            {
                "protocol": worker.PROTOCOL,
                "command": "FINISH",
                "readyEventId": CLOSED if case == "wrong-ready" else READY,
            }
        )
        payload = line(start()) + (b"" if case == "eof" else command)
        if case == "extra-command":
            payload += command
        os.write(sender, payload)
        os.close(sender)
        sender = -1
        env = (
            {}
            if case == "missing-env"
            else {
                "ACCESSFORGE_DATABASE_URL": "private-product",
                "ACCESSFORGE_OBSERVER_DATABASE_URL": "private-source",
            }
        )
        if case == "complete":
            worker.run_private_host(incoming, outgoing, env, lambda _seconds: None)
        else:
            with pytest.raises(ValueError):
                worker.run_private_host(incoming, outgoing, env, lambda _seconds: None)
        os.close(outgoing)
        outgoing = -1
        raw = os.read(receiver, 4096)
        receipts = [json.loads(item) for item in raw.splitlines()]
        if case == "complete":
            assert calls == ["construct", "begin", "finish", "abort"]
            assert [r["status"] for r in receipts] == ["READY_RETAINED", "CLOSED_RETAINED"]
            assert [r["eventId"] for r in receipts] == [READY, CLOSED]
        else:
            assert len(receipts) == (0 if case in {"missing-env", "begin-failure"} else 1)
            assert "finish" not in calls or case == "finish-failure"
            if case != "missing-env":
                assert calls[-1] == "abort"
        assert b"private" not in raw
        assert all(
            set(r) == {"protocol", "status", "workspaceId", "runId", "attemptId", "eventId"}
            for r in receipts
        )
    finally:
        for fd in (incoming, sender, receiver, outgoing):
            if fd >= 0:
                os.close(fd)


@pytest.mark.skipif(os.name != "posix", reason="private collector process is POSIX-only")
@pytest.mark.parametrize("interruption", ["timeout", "terminate"])
def test_real_process_interruption_never_emits_closed_receipt(interruption: str) -> None:
    # Test-only observer substitution; exercise actual inherited pipes, main, signals and exit.
    script = """
import os, sys
from accessforge_orchestrator import reference_effect_worker as worker
incoming, outgoing = os.dup(int(sys.argv[1])), os.dup(int(sys.argv[2]))
os.dup2(incoming, 3)
os.dup2(outgoing, 4)
class SyntheticObserver:
    def __init__(self, *args, **kwargs): pass
    def begin(self): return '11111111-1111-4111-8111-111111111111'
    def finish(self): raise AssertionError('no FINISH or STOP supplied')
    def abort(self): print('aborted', flush=True)
worker.ReferenceEffectObserver = SyntheticObserver
sys.argv = ['reference_effect_worker', '--private-host']
raise SystemExit(worker.main())
"""
    incoming, sender = os.pipe()
    receiver, outgoing = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed test program, isolated fake environment
            [sys.executable, "-I", "-c", script, str(incoming), str(outgoing)],
            pass_fds=(incoming, outgoing),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "ACCESSFORGE_DATABASE_URL": "dbname=synthetic-product",
                "ACCESSFORGE_OBSERVER_DATABASE_URL": "dbname=synthetic-reference",
            },
        )
        os.close(incoming)
        incoming = -1
        os.close(outgoing)
        outgoing = -1
        os.write(
            sender, line({**start(), "maxWallSeconds": 1 if interruption == "timeout" else 10})
        )
        assert select.select([receiver], [], [], 5)[0], "child readiness unavailable"
        first = os.read(receiver, 4096)
        assert json.loads(first)["status"] == "READY_RETAINED"
        if interruption == "terminate":
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=4)
        assert process.returncode == 1
        assert stdout == b"aborted\n"
        assert (
            stderr == b"collector lifecycle unconfirmed; reconcile original records, do not retry\n"
        )
        assert os.read(receiver, 4096) == b""
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        for fd in (incoming, sender, receiver, outgoing):
            if fd >= 0:
                os.close(fd)
