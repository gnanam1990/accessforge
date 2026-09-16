"""Pure request contract shared by the control plane and privileged diagnosis worker."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from .canonical import canonicalize, digest


def model_profile() -> dict[str, Any]:
    """Pinned reviewed profile; changing it changes the consent digest, never silently reuses it."""
    return {
        "provider": "codex-chatgpt",
        "sdk_version": "0.154.0",
        "model_id": "gpt-6-astra",
        "budget_semantics": "RESULT_ADMISSION_NOT_SPEND_CAP",
        "invocation_output_tokens": 2000,
        "invocation_total_tokens": 20000,
        "max_context_characters": 50000,
        "call_timeout_seconds": 45.0,
    }


def _text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def validate(payload: dict[str, Any]) -> None:
    if len(canonicalize(payload)) > 28000:
        raise ValueError("bounded diagnosis request required")
    fields = {
        "manifestDigest",
        "evaluationDigest",
        "modelProfileDigest",
        "assertionId",
        "componentPath",
        "componentName",
        "excerpts",
        "supersedes",
        "billableCallAcknowledged",
    }
    if set(payload) != fields or payload["billableCallAcknowledged"] is not True:
        raise ValueError("exact diagnosis scope and explicit billable-call acknowledgment required")
    for key in ("manifestDigest", "evaluationDigest", "modelProfileDigest"):
        if not isinstance(payload[key], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[key]):
            raise ValueError("exact diagnosis digests required")
    if payload["modelProfileDigest"] != digest(model_profile()):
        raise ValueError("diagnosis model profile changed; review the current profile")
    if not _text(payload["assertionId"], 200) or not _text(payload["componentName"], 300):
        raise ValueError("bounded diagnosis assertion and component required")
    predecessor = payload["supersedes"]
    if predecessor is not None and (
        not isinstance(predecessor, str) or str(UUID(predecessor)) != predecessor
    ):
        raise ValueError("canonical diagnosis predecessor required")
    excerpts = payload["excerpts"]
    if not isinstance(excerpts, list) or not 1 <= len(excerpts) <= 40:
        raise ValueError("one to forty bounded source excerpts required")
    paths: set[str] = set()
    for excerpt in excerpts:
        if not isinstance(excerpt, dict) or set(excerpt) != {"path", "lineStart", "lineEnd"}:
            raise ValueError("closed source excerpt required")
        path = excerpt["path"]
        if not _text(path, 500) or "\\" in path or "\x00" in path:
            raise ValueError("relative source path required")
        parsed = PurePosixPath(path)
        if not parsed.parts or parsed.is_absolute() or ".." in parsed.parts or str(parsed) != path:
            raise ValueError("canonical relative source path required")
        first, last = excerpt["lineStart"], excerpt["lineEnd"]
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= first + 199:
            raise ValueError("source excerpt must contain one to two hundred lines")
        paths.add(path)
    if not isinstance(payload["componentPath"], str) or payload["componentPath"] not in paths:
        raise ValueError("component must be in the requested excerpts")
