"""Operator comparison export, with separately explicit durable-retention authority."""

from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path
from threading import Event
from uuid import UUID

from .comparison import prepare_and_retain_comparison, prepare_comparison


def _identifier(value: str) -> str:
    try:
        if str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("use a canonical lowercase UUID")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read committed original source and a stored proposal; no patch/build/model executes."
        )
    )
    parser.add_argument("--workspace-id", required=True, type=_identifier)
    parser.add_argument("--project-id", required=True, type=_identifier)
    parser.add_argument("--patch-id", required=True, type=_identifier)
    parser.add_argument("--actor-id", required=True, type=_identifier)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument(
        "--retain-record",
        action="store_true",
        help=("Retain a comparison for workspace review; requires project-configure permission."),
    )
    parser.add_argument(
        "--include-source",
        action="store_true",
        help="Explicitly emit source text to this terminal/stdout.",
    )
    args = parser.parse_args(argv)
    if not args.include_source and not args.retain_record:
        parser.error("explicit --include-source or --retain-record is required")
    fence = Event()
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in handlers:
            signal.signal(sig, lambda _sig, _frame: fence.set())
        repository = args.repository.resolve(strict=True)
        if not repository.is_dir():
            raise ValueError("operator repository must be a directory")
        prepare = prepare_and_retain_comparison if args.retain_record else prepare_comparison
        result = prepare(
            os.environ["ACCESSFORGE_DATABASE_URL"],
            workspace_id=args.workspace_id,
            patch_id=args.patch_id,
            actor_id=args.actor_id,
            repositories={args.project_id: repository},
            cancelled=fence.is_set,
        )
        if fence.is_set():
            raise InterruptedError("comparison export cancelled")
        if args.retain_record and not args.include_source:
            result = {key: value for key, value in result.items() if key != "comparison"}
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    except Exception as exc:
        # Repository paths, raw source and database connection strings stay out of failure output.
        print(
            json.dumps(
                {
                    "status": "RETENTION_UNCONFIRMED"
                    if args.retain_record
                    else "COMPARISON_UNAVAILABLE",
                    "errorType": type(exc).__name__,
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
