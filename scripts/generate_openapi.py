"""Regenerate `contracts/openapi.json` from the live application.

Generated from the app rather than hand-written, and committed so CI can diff the two. The module
prompt asks for generate-and-diff in CI rather than trusting checked-in output, and the reason is
specific: an OpenAPI document maintained by hand describes what someone believed the API did. This
one is produced by the same `create_app` a request goes through, so a route that changed shape
without the contract changing is a diff, not a discovery six months later.

The placeholder configuration below never connects to anything. `create_app` only reads settings;
the schema comes from the route signatures.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts" / "openapi.json"

_PLACEHOLDERS = {
    "ACCESSFORGE_DATABASE_URL": "postgresql://schema-generation-only@127.0.0.1:5432/unused",
    "ACCESSFORGE_EVIDENCE_ENDPOINT_URL": "http://127.0.0.1:9000",
    "ACCESSFORGE_EVIDENCE_BUCKET": "unused",
    "ACCESSFORGE_EVIDENCE_ACCESS_KEY": "unused",
    "ACCESSFORGE_EVIDENCE_SECRET_KEY": "unused",
}


def render() -> str:
    for key, value in _PLACEHOLDERS.items():
        os.environ.setdefault(key, value)
    from accessforge_api.app import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            print(
                "contracts/openapi.json is out of date. The API changed shape without the contract "
                "changing with it. Run: uv run python scripts/generate_openapi.py",
                file=sys.stderr,
            )
            return 1
        print("contract matches the live application")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
