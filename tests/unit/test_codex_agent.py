"""Synthetic CLI protocol checks, separate from live OAuth/provider acceptance."""

import asyncio
import json
import sys
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel

import accessforge_orchestrator.codex_agent as module
from accessforge_orchestrator.codex_agent import (
    CodexStructuredAgent,
    CodexUnavailable,
    _command,
    _environment,
)


def events() -> list[dict]:
    return [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"ok":true}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]


async def read(items: list[dict], fence: Event | None = None) -> str:
    stream = asyncio.StreamReader()
    for item in items:
        stream.feed_data(json.dumps(item).encode() + b"\n")
    stream.feed_eof()
    proc = cast(asyncio.subprocess.Process, SimpleNamespace(stdout=stream))
    return await CodexStructuredAgent._read(
        proc, {"turns": 1, "total_tokens": 20, "output_tokens": 8}, fence or Event()
    )


@pytest.mark.asyncio
async def test_complete_structured_turn() -> None:
    assert await read(events()) == '{"ok":true}'


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "tool", "double", "usage", "over", "after", "error"])
async def test_no_draft_from_uncertain_or_out_of_scope_stream(case: str) -> None:
    items = events()
    if case == "missing":
        items.pop()
    elif case == "tool":
        items[2]["item"]["type"] = "command_execution"
    elif case == "double":
        items.insert(3, items[2])
    elif case == "usage":
        items[-1]["usage"]["input_tokens"] = True
    elif case == "over":
        items[-1]["usage"]["output_tokens"] = 99
    elif case == "after":
        items.append(items[-1])
    else:
        items[-1] = {"type": "turn.failed"}
    with pytest.raises(CodexUnavailable):
        await read(items)


@pytest.mark.asyncio
async def test_cancellation_no_output_admitted() -> None:
    fence = Event()
    fence.set()
    with pytest.raises(CodexUnavailable, match="cancelled"):
        await read(events(), fence)


def test_command_has_no_user_config_or_executable_tool_capabilities() -> None:
    args = _command("/operator/codex", "/private/empty", "gpt-6-astra")
    assert args[0] == "/operator/codex"
    assert "--ignore-user-config" in args and "--ignore-rules" in args
    assert "--ephemeral" in args and "read-only" in args
    assert 'forced_login_method="chatgpt"' in args
    for feature in ("shell_tool", "unified_exec", "apps", "hooks", "plugins", "multi_agent"):
        assert f"features.{feature}=false" in args
    assert 'web_search="disabled"' in args
    assert args[-1] == "-"


def test_no_ambient_service_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "ACCESSFORGE_DATABASE_URL", "NODE_OPTIONS"):
        monkeypatch.setenv(key, "never-forward")
    assert "never-forward" not in _environment().values()


@pytest.mark.asyncio
async def test_timeout_reaps_original_child(monkeypatch: pytest.MonkeyPatch) -> None:
    class Draft(BaseModel):
        answer: str

    children: list[asyncio.subprocess.Process] = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        proc = await original_spawn(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(module, "_preflight", lambda: (sys.executable, _environment()))
    monkeypatch.setattr(
        module, "_command", lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            CodexStructuredAgent(system_prompt="test").invoke_async(
                "test",
                structured_output_model=Draft,
                limits={"turns": 1, "output_tokens": 8, "total_tokens": 20},
                cancel_signal=Event(),
            ),
            timeout=0.3,
        )
    assert len(children) == 1 and children[0].returncode is not None
