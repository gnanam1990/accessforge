"""Read one bounded workspace/run recovery page without probing a reader or application."""

import argparse
import json
import os
from uuid import UUID

from accessforge_persistence import connect, effect_recovery


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Inspect original form transport; never retry/reset"
    )
    parser.add_argument("--workspace-id", required=True, type=UUID)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--after", type=UUID)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    try:
        with connect(os.environ["ACCESSFORGE_DATABASE_URL"]) as conn, conn.transaction():
            # Read-only before the first query, then the exact transaction-local RLS scope.
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(
                "SELECT set_config('accessforge.workspace_id', %s, true)", (str(args.workspace_id),)
            )
            result = effect_recovery.read(
                conn,
                run_id=str(args.run_id),
                limit=args.limit,
                after=None if args.after is None else str(args.after),
            )
    except Exception:
        parser.exit(
            2, "FORM_TRANSPORT_HISTORY_UNAVAILABLE; no retry, reset or model call attempted.\n"
        )
    if result is None:
        parser.exit(1, "RUN_UNAVAILABLE in the selected workspace.\n")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
