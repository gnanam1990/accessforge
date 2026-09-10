"""Apply pending migrations. A deliberate, single, separate step.

Not run from application startup, and the reason is worth stating where an operator will read it:
two replicas booting together would run this concurrently, and a binary that migrated on boot would
migrate *forward* again during a rollback -- turning a rollback into a second upgrade.

Ordering for a deploy is therefore fixed and one-directional:

    1. take a backup (scripts/backup.py) -- a migration is the change a restore exists for
    2. run this
    3. start the new binaries

Readiness (`/health/ready`) reports the result of step 2 rather than performing it, so a process
that came up against an un-migrated database answers 503 with the reason instead of serving.
"""

from __future__ import annotations

import argparse
import os
import sys

from accessforge_persistence import applied_migrations, expected_migrations, migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ACCESSFORGE_DATABASE_URL"),
        help="defaults to ACCESSFORGE_DATABASE_URL",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "report what would be applied and exit non-zero if anything is pending, without "
            "applying it. For a deploy gate that must not itself mutate the database."
        ),
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        print(
            "no database url: pass --database-url or set ACCESSFORGE_DATABASE_URL",
            file=sys.stderr,
        )
        return 2

    expected = expected_migrations()
    done = set(applied_migrations(args.database_url))
    pending = [name for name in expected if name not in done]

    # Reported before anything is applied, and reported the same way in both modes: an operator
    # reading --check output and then running for real should see the same list, not a summary in
    # one mode and detail in the other.
    unknown = sorted(done - set(expected))
    if unknown:
        print(
            "this database records migrations this tree does not contain: "
            + ", ".join(unknown)
            + "\nIt was written by a newer release. Bring the code forward; a code rollback does "
            "not reverse a data migration.",
            file=sys.stderr,
        )
        return 1

    if args.check:
        if pending:
            print("pending:\n  " + "\n  ".join(pending), file=sys.stderr)
            return 1
        print(f"up to date at {expected[-1]}")
        return 0

    applied = migrate(args.database_url)
    if applied:
        print("applied:\n  " + "\n  ".join(applied))
    else:
        print(f"nothing to apply; already at {expected[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
