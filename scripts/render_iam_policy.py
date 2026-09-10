"""Strip the annotations out of a proposed IAM policy so it can actually be attached.

The files in `infra/aws/iam/` carry `"//"` members explaining what each statement is for and what
breaks without it. Those explanations are the most valuable thing in them — a policy nobody can
explain is a policy that gets widened during an incident and never narrowed afterwards — and
they are also not legal IAM. JSON has no comments, so `"//"` is a data member, and IAM rejects
a policy document containing elements it does not recognise.

So the annotated file is the source and this is the renderer. The alternative arrangements are both
worse: moving the notes to Markdown separates each explanation from the statement it explains, and
attaching the annotated file produces a `MalformedPolicyDocument` error at the one moment
somebody is trying to provision something.

    uv run python scripts/render_iam_policy.py infra/aws/iam/control-plane-task.json
    uv run python scripts/render_iam_policy.py --check infra/aws/iam/*.json

`--check` validates structure without writing anything: every statement has an Effect, an Action and
a Resource, every Sid is unique, and nothing is left that IAM would reject. It does **not** validate
against AWS — that needs credentials this environment does not have, and a renderer that claimed
otherwise would be making exactly the kind of unearned claim this product exists to refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: Members that exist to explain and must not reach AWS. Any key beginning with `//` is an
#: annotation, which lets a file carry `"//scope"`, `"//why"` and so on without this needing to know
#: their names.
ANNOTATION_PREFIX = "//"

#: What IAM accepts at the top level of a policy document, and inside a statement. An allowlist
#: rather than a denylist of annotations: a typo'd member name would otherwise be rendered straight
#: through into a document AWS refuses, and its error names the document rather than the key.
POLICY_MEMBERS = frozenset({"Version", "Id", "Statement"})
STATEMENT_MEMBERS = frozenset(
    {
        "Sid",
        "Effect",
        "Principal",
        "NotPrincipal",
        "Action",
        "NotAction",
        "Resource",
        "NotResource",
        "Condition",
    }
)


def strip_annotations(value: Any) -> Any:
    """Remove every `//`-prefixed member, recursively."""
    if isinstance(value, dict):
        return {
            k: strip_annotations(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.startswith(ANNOTATION_PREFIX))
        }
    if isinstance(value, list):
        return [strip_annotations(v) for v in value]
    return value


def problems_with(document: dict[str, Any], *, source: str) -> list[str]:
    """Everything structurally wrong with a rendered document, all of it, in one pass."""
    found: list[str] = []

    unexpected = sorted(set(document) - POLICY_MEMBERS)
    if unexpected:
        found.append(f"{source}: unexpected top-level member(s): {', '.join(unexpected)}")
    if document.get("Version") != "2012-10-17":
        found.append(f"{source}: Version must be '2012-10-17', got {document.get('Version')!r}")

    statements = document.get("Statement")
    if not isinstance(statements, list) or not statements:
        found.append(f"{source}: Statement must be a non-empty list")
        return found

    seen: set[str] = set()
    for index, statement in enumerate(statements):
        where = f"{source}: statement {index}"
        if not isinstance(statement, dict):
            found.append(f"{where} is not an object")
            continue
        extra = sorted(set(statement) - STATEMENT_MEMBERS)
        if extra:
            found.append(f"{where}: unexpected member(s): {', '.join(extra)}")
        if statement.get("Effect") not in {"Allow", "Deny"}:
            found.append(f"{where}: Effect must be Allow or Deny")
        if not (statement.get("Action") or statement.get("NotAction")):
            found.append(f"{where}: needs an Action or NotAction")
        if not (statement.get("Resource") or statement.get("NotResource")):
            found.append(f"{where}: needs a Resource or NotResource")
        sid = statement.get("Sid")
        if sid is not None:
            if sid in seen:
                found.append(f"{where}: duplicate Sid {sid!r}")
            seen.add(str(sid))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate structure and print nothing on success; write no files",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="write rendered documents here instead of to stdout",
    )
    args = parser.parse_args(argv)

    failures: list[str] = []
    for path in args.paths:
        rendered = strip_annotations(json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(rendered, dict):
            failures.append(f"{path}: not a JSON object")
            continue
        failures.extend(problems_with(rendered, source=str(path)))
        if args.check:
            continue
        body = json.dumps(rendered, indent=2) + "\n"
        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            destination = args.out_dir / path.name
            destination.write_text(body, encoding="utf-8")
            print(f"wrote {destination}")
        else:
            print(body, end="")

    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    if args.check:
        print(f"{len(args.paths)} policy document(s) render to valid IAM structure")
        print("NOTE: structure only. Nothing here was validated against AWS; no credentials exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
