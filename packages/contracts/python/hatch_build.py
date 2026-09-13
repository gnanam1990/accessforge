"""Ship authoritative schemas in direct wheels and wheels rebuilt from an sdist."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol


class HookContext(Protocol):
    @property
    def root(self) -> str: ...

    @property
    def target_name(self) -> str: ...


def initialize(self: HookContext, version: str, build_data: dict[str, Any]) -> None:
    root = Path(self.root)
    bundled = root / "src/accessforge_contracts/schemas"
    shared = root.parent / "schemas"
    if bundled.is_dir():
        # Already inside an unpacked sdist: normal package discovery includes these once.
        if not (bundled / "run-manifest.schema.json").is_file():
            raise ValueError("source distribution is missing its run manifest schema")
        return
    if not (shared / "run-manifest.schema.json").is_file():
        raise ValueError("authoritative contract schemas are missing")
    destination = (
        "src/accessforge_contracts/schemas"
        if self.target_name == "sdist"
        else "accessforge_contracts/schemas"
    )
    build_data["force_include"][str(shared)] = destination


def get_build_hook() -> type[Any]:
    # Hatchling is an isolated build dependency, not an application runtime dependency. Keep the
    # thin framework adapter lazy; the complete file-selection policy above is independently typed.
    base = importlib.import_module("hatchling.builders.hooks.plugin.interface").BuildHookInterface
    return type("ContractSchemasHook", (base,), {"initialize": initialize})
