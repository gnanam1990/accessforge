"""Validate an installed source bundle without starting services, readers or model calls."""

from __future__ import annotations

import importlib
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    modules = (
        "accessforge_api.app",
        "accessforge_orchestrator.navigator.operator",
        "accessforge_orchestrator.diagnosis.operator",
        "accessforge_orchestrator.repair.operator",
    )
    for name in modules:
        module = importlib.import_module(name)
        if module.__file__ is None or not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError("release check imported code outside the extracted source")
    from accessforge_contracts import available_schemas, load_schema
    from accessforge_persistence import expected_migrations

    schemas = available_schemas()
    if not schemas:
        raise RuntimeError("release schemas are missing")
    for schema in schemas:
        load_schema(schema)
    migrations = expected_migrations()
    print(
        json.dumps(
            {
                "kind": "INSTALLED_SOURCE_IMPORT_CHECK_NOT_SERVICE_ACCEPTANCE",
                "modulesImported": len(modules),
                "schemasLoaded": len(schemas),
                "migrationFiles": len(migrations),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
