"""Schema loading and validation at trust boundaries.

JSON Schema is authoritative. This package loads those files and validates against them; it never
restates a rule in Python, because two definitions of the same constraint will eventually
disagree and the disagreement will be silent.

Static typing is not input validation. A `dict[str, str]` annotation says what the code expects,
not what arrived over the wire, so anything crossing a trust boundary is validated here at
runtime.
"""

from __future__ import annotations

import json
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def _resolve_schema_dir() -> Path:
    """Locate the schema directory in an installed wheel or in the source tree.

    An installed wheel carries the schemas alongside the package (see the force-include in
    pyproject.toml). In the editable workspace checkout they live at their authoritative shared
    location, two levels up, where the TypeScript package reads them from too.
    """
    packaged = Path(__file__).resolve().parent / "schemas"
    if packaged.is_dir():
        return packaged
    # packages/contracts/python/src/accessforge_contracts/ -> packages/contracts/schemas
    return Path(__file__).resolve().parents[3] / "schemas"


SCHEMA_DIR = _resolve_schema_dir()

__all__ = [
    "SCHEMA_DIR",
    "SchemaValidationError",
    "available_schemas",
    "load_schema",
    "schema_digest",
    "validate",
]


class SchemaValidationError(ValueError):
    """A payload failed schema validation at a trust boundary."""

    def __init__(self, schema_name: str, errors: list[str]) -> None:
        self.schema_name = schema_name
        self.errors = errors
        super().__init__(f"{schema_name}: " + "; ".join(errors))


@cache
def available_schemas() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in SCHEMA_DIR.glob("*.schema.json")))


@cache
def _cached_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"no schema {name!r} in {SCHEMA_DIR}; available: {', '.join(available_schemas())}"
        )
    return dict(json.loads(path.read_text(encoding="utf-8")))


def load_schema(name: str) -> dict[str, Any]:
    """Return a schema as a fresh deep copy.

    The cache is private. Returning the cached object let a caller mutate it — dropping
    ``additionalProperties``, say — and weaken validation for every later call in the process.
    """
    return deepcopy(_cached_schema(name))


@cache
def _registry() -> Registry:
    """Resolve local $ref targets without reaching the network.

    A validator that fetched schemas over HTTP would make validation depend on connectivity and
    on whatever is served at that URL today.
    """
    resources = []
    for name in available_schemas():
        schema = _cached_schema(name)
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        resources.append((name, resource))
        if "$id" in schema:
            resources.append((str(schema["$id"]), resource))
    return Registry().with_resources(resources)


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_cached_schema(name), registry=_registry())


def validate(name: str, payload: Any) -> None:
    """Validate ``payload`` against the named schema, or raise.

    Every error is reported, not just the first: a caller fixing one field at a time cannot see
    the shape of what is wrong.
    """
    errors: list[ValidationError] = sorted(
        _validator(name).iter_errors(payload), key=lambda e: list(e.absolute_path)
    )
    if errors:
        raise SchemaValidationError(
            name,
            [
                f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                for e in errors
            ],
        )


def schema_digest(name: str) -> str:
    """Digest of a schema's canonical form, used to detect drift between schemas and bindings."""
    from accessforge_domain.canonical import digest  # local import keeps the dependency one-way

    return digest(_cached_schema(name))
