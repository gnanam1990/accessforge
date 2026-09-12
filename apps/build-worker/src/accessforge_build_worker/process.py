"""Bounded pipes for supervisor-owned Docker commands, never a host source executor."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass


class CommandStopped(RuntimeError):
    """A deadline, cancellation or output bound ended observation; not successful execution."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    code: int
    stdout: bytes
    stderr: bytes


def run_bounded(
    argv: tuple[str, ...],
    *,
    deadline: float,
    output_limit: int,
    input_bytes: bytes | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> CommandResult:
    """Drain every pipe without unbounded communicate() buffers or producer deadlock.

    Killing a Docker CLI is NOT stop proof for its container. The sandbox caller must independently
    remove/reconcile its exact container after any CommandStopped result.
    """
    if output_limit < 1:
        raise ValueError("output_limit must be positive")
    if cancelled():
        raise CommandStopped("cancelled")
    if time.monotonic() >= deadline:
        raise CommandStopped("deadline exceeded")
    stdout = bytearray()
    stderr = bytearray()
    position = 0
    completed = False
    with subprocess.Popen(  # noqa: S603 - supervisor constructs argv; no shell on the host.
        argv,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        assert process.stdout is not None and process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                for pipe, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, label)
                if process.stdin is not None:
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                while selector.get_map():
                    if cancelled():
                        raise CommandStopped("cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CommandStopped("deadline exceeded")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        if key.data == "stdin":
                            assert process.stdin is not None and input_bytes is not None
                            try:
                                written = os.write(key.fd, input_bytes[position : position + 65536])
                                position += written
                            except BrokenPipeError:
                                position = len(input_bytes)
                            except BlockingIOError:
                                continue
                            if position == len(input_bytes):
                                selector.unregister(key.fileobj)
                                process.stdin.close()
                        else:
                            try:
                                data = os.read(key.fd, 65536)
                            except BlockingIOError:
                                continue
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            if len(stdout) + len(stderr) + len(data) > output_limit:
                                raise CommandStopped("output limit exceeded")
                            (stdout if key.data == "stdout" else stderr).extend(data)
                while process.poll() is None:
                    if cancelled():
                        raise CommandStopped("cancelled")
                    if time.monotonic() >= deadline:
                        raise CommandStopped("deadline exceeded")
                    time.sleep(0.01)
            result = CommandResult(process.wait(), bytes(stdout), bytes(stderr))
            completed = True
            return result
        finally:
            # The process group also covers local CLI children holding pipes open after a timeout.
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
