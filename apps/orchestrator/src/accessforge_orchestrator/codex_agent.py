"""One-shot Codex OAuth structured proposals; never an autonomous source editor.

Codex owns authentication. This adapter never reads auth.json, copies tokens or falls back to
API-key billing. Token ceilings are result-admission limits, not prepaid spending guarantees.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any, Protocol

from pydantic import BaseModel
from strands.types.agent import Limits

CODEX_VERSION = "0.154.0"
CODEX_MODEL = "gpt-6-astra"
MAX_LINE = 512 * 1024
MAX_STREAM = 2 * 1024 * 1024


class CodexUnavailable(RuntimeError):
    """Static errors only: provider stderr, source and credentials must not escape."""


class ProposalResult(Protocol):
    @property
    def structured_output(self) -> Any: ...


@dataclass(frozen=True)
class StructuredResult:
    structured_output: BaseModel


def structured_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Require explicit values in the wire schema without weakening domain validation.

    Nullable fields remain nullable; defaulted non-null fields must be returned explicitly.
    Pydantic still validates the final response with its original constraints and validators.
    """
    schema = model.model_json_schema()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    raise ValueError("Codex proposals require closed object schemas")
                node["required"] = list(node.get("properties", {}))
            for key in ("properties", "$defs", "definitions", "patternProperties"):
                for child in node.get(key, {}).values():
                    visit(child)
            for key in ("items", "anyOf", "allOf", "oneOf", "prefixItems"):
                if key in node:
                    visit(node[key])

    visit(schema)
    return schema


def _environment() -> dict[str, str]:
    # Keep the user's existing OAuth store; do not propagate API keys, database/object-store
    # credentials, proxy settings, NODE_OPTIONS, PYTHONPATH or AWS credential chains.
    keys = ("HOME", "CODEX_HOME", "PATH", "TMPDIR")
    return {key: os.environ[key] for key in keys if key in os.environ}


def _command(executable: str, root: str, model: str) -> list[str]:
    args = [
        executable,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--cd",
        root,
        "--model",
        model,
        "--json",
        "--color",
        "never",
        "--output-schema",
        str(Path(root) / "output.schema.json"),
    ]
    settings: dict[str, Any] = {
        "model_provider": "openai",
        "forced_login_method": "chatgpt",
        "approval_policy": "never",
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
        "model_reasoning_effort": "low",
        "suppress_unstable_features_warning": True,
        "tools.view_image": False,
    }
    for name in (
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "remote_plugin",
        "hooks",
        "memories",
        "multi_agent",
        "multi_agent_v2",
        "browser_use",
        "computer_use",
        "image_generation",
        "workspace_dependencies",
        "goals",
        "tool_suggest",
        "skill_mcp_dependency_install",
        "shell_snapshot",
        "unbounded_connection_retries",
    ):
        settings[f"features.{name}"] = False
    settings["features.skip_host_skill_discovery"] = True
    for key, value in settings.items():
        args.extend(("-c", f"{key}={json.dumps(value)}"))
    return [*args, "-"]


def _preflight() -> tuple[str, dict[str, str]]:
    executable = shutil.which("codex")
    if executable is None:
        raise CodexUnavailable("Codex CLI is not installed")
    env = _environment()
    for arguments, expected in (
        (["--version"], f"codex-cli {CODEX_VERSION}"),
        (["login", "status"], "Logged in using ChatGPT"),
    ):
        result = subprocess.run(  # noqa: S603 - local operator CLI, fixed read-only arguments
            [executable, *arguments], env=env, capture_output=True, timeout=5, check=False
        )
        if result.returncode or (result.stdout + result.stderr).decode().strip() != expected:
            raise CodexUnavailable("Pinned Codex CLI with ChatGPT login required")
    return executable, env


class CodexStructuredAgent:
    def __init__(self, *, system_prompt: str, model_id: str = CODEX_MODEL) -> None:
        if model_id != CODEX_MODEL:
            raise ValueError("Codex model differs from the reviewed profile")
        self.system_prompt, self.model_id = system_prompt, model_id

    async def invoke_async(
        self,
        prompt: str,
        *,
        structured_output_model: type[BaseModel],
        limits: Limits,
        cancel_signal: Event,
    ) -> StructuredResult:
        if cancel_signal.is_set() or limits.get("turns") != 1:
            raise CodexUnavailable("One uncancelled Codex turn required")
        executable, env = await asyncio.to_thread(_preflight)
        if cancel_signal.is_set():
            raise CodexUnavailable("Cancelled before Codex invocation")
        with TemporaryDirectory(prefix="accessforge-codex-") as root:
            Path(root, "output.schema.json").write_text(
                json.dumps(structured_schema(structured_output_model)), encoding="utf-8"
            )
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *_command(executable, root, self.model_id),
                    env=env,
                    cwd=root,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                    limit=MAX_LINE,
                )
            )
            try:
                proc = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                # Keep the original spawn handle: cancellation must not orphan a child whose
                # PID arrives after the caller times out. No prompt has been written yet.
                proc = await spawning
                await _stop(proc)
                raise
            try:
                if cancel_signal.is_set():
                    raise CodexUnavailable("Cancelled before Codex prompt submission")
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write((self.system_prompt + "\n\n" + prompt).encode())
                await proc.stdin.drain()
                proc.stdin.close()
                output = await self._read(proc, limits, cancel_signal)
                if await proc.wait() != 0 or cancel_signal.is_set():
                    raise CodexUnavailable("Codex did not finish successfully")
                return StructuredResult(structured_output_model.model_validate_json(output))
            finally:
                # No retry/resume after timeout, cancellation, malformed output or lost response.
                await _stop(proc)

    @staticmethod
    async def _read(proc: asyncio.subprocess.Process, limits: Limits, fence: Event) -> str:
        assert proc.stdout is not None
        total = 0
        started = completed = False
        output: str | None = None
        while True:
            if fence.is_set():
                raise CodexUnavailable("Codex invocation cancelled")
            # A retained read task avoids discarding partial frames on cancellation polls.
            read = asyncio.create_task(proc.stdout.readline())
            try:
                while not read.done():
                    await asyncio.wait({read}, timeout=0.1)
                    if fence.is_set():
                        raise CodexUnavailable("Codex invocation cancelled")
                line = read.result()
            finally:
                if not read.done():
                    read.cancel()
                    await asyncio.gather(read, return_exceptions=True)
            if not line:
                break
            total += len(line)
            if total > MAX_STREAM:
                raise CodexUnavailable("Codex output exceeded the byte envelope")
            event = json.loads(line)
            if not isinstance(event, dict):
                raise CodexUnavailable("Invalid Codex event")
            kind = event.get("type")
            if completed:
                raise CodexUnavailable("Unexpected event after Codex completion")
            if kind == "thread.started" and not started:
                continue
            if kind == "turn.started" and not started:
                started = True
                continue
            if kind in ("item.started", "item.updated", "item.completed") and started:
                item = event.get("item", {})
                if item.get("type") not in ("agent_message", "reasoning"):
                    raise CodexUnavailable("Codex attempted a non-proposal operation")
                if kind == "item.completed" and item.get("type") == "agent_message":
                    if output is not None or not isinstance(item.get("text"), str):
                        raise CodexUnavailable("One structured Codex response required")
                    output = item["text"]
                continue
            if kind == "turn.completed" and started:
                usage = event.get("usage", {})
                incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
                if (
                    type(incoming) is not int
                    or type(outgoing) is not int
                    or incoming < 0
                    or outgoing < 0
                    or outgoing > limits.get("output_tokens", 0)
                    or incoming + outgoing > limits.get("total_tokens", 0)
                ):
                    raise CodexUnavailable("Codex usage absent or outside result-admission limits")
                completed = True
                continue
            raise CodexUnavailable("Codex stream failed or violated the one-turn protocol")
        if not completed or output is None:
            raise CodexUnavailable("No complete Codex structured response")
        return output


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
